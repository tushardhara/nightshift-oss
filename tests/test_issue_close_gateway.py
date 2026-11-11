"""#424 / ADR-0055 — a ticket closes only through the disposition gateway.

WHAT THIS SUITE HAS TO PROVE, and why the shape is what it is
-------------------------------------------------------------
Issue #424 was filed off a measurement: on 2026-08-26, **62 issues closed
inside one 60-second window**, and that batch swept three live P0s (#118,
#129, #158 — the last with no closing comment at all). So the tests that
matter here are not "does the happy path work". They are the REFUSALS, and
AC-8 says so explicitly: one test per rejection path in AC-2, AC-5 and AC-6,
each with a matching positive case that IS accepted.

The positive case in each pair is load-bearing, not decoration. A gateway
that refuses everything would satisfy every refusal assertion on its own and
be completely useless; the pair is what distinguishes "this rule fires" from
"this command is broken" (issue #81: a test that passes no matter what is
worth less than no test).

HERMETIC, on the two seams the ADR froze
----------------------------------------
* **GitHub** is faked through ``$NIGHTSHIFT_CTL_GH`` — the same seam
  ``tests/test_pr_head.py`` uses. No network, no credential, and above all no
  real ``gh issue close``: a test suite for a close gateway that could close
  real issues would be its own worst bug.
* **Time** is virtual, via ``Ctl(clock=VIRTUAL_T0)`` and ``--now`` on argv
  (``tests/ctlutil.py``). AC-6's 60-second window is proven by ADVANCING the
  clock, never by sleeping — so the window test is deterministic and costs
  microseconds rather than a minute.
"""

import json
import os
import sqlite3
import unittest

from ctlutil import Ctl, VIRTUAL_T0

OPEN_PLAIN = {"state": "OPEN", "labels": []}
OPEN_P0 = {"state": "OPEN", "labels": [{"name": "P0"}]}
OPEN_SECURITY = {"state": "OPEN", "labels": [{"name": "security"}]}
GOOD_OID = "a" * 40


class GatewayCase(unittest.TestCase):
    """Base fixture: a control plane, a milestone registry, and a fake gh."""

    # Every issue the fake knows about, keyed by number. Subclasses and
    # individual tests mutate this through `self.set_issues(...)`.
    ISSUES = {"500": OPEN_PLAIN}

    def setUp(self):
        self.c = Ctl(clock=VIRTUAL_T0)

        # AC-3 validates against `## M<N>` headers in this file, NOT against
        # GitHub Milestone objects — this repo has zero of those
        # (`gh api .../milestones` -> []), which is why the ADR picked the
        # markdown registry. `_repo_root_for` derives this path from the db
        # location, so writing it into the Ctl tmpdir is all that is needed.
        prd = os.path.join(self.c.dir, "docs", "prd")
        os.makedirs(prd, exist_ok=True)
        with open(os.path.join(prd, "MILESTONES.md"), "w") as handle:
            handle.write(
                "# Milestones\n\n"
                "## M1 — the org can run\n\ntext\n\n"
                "## M4 — it survives days and crashes\n\ntext\n")

        self.state_path = os.path.join(self.c.dir, "gh-state.json")
        self.argv_log = os.path.join(self.c.dir, "gh-argv.log")
        self.gh_path = os.path.join(self.c.dir, "fake-gh")
        self._write_gh()
        self.set_issues(dict(self.ISSUES))

        self._saved_env = {}
        for key, value in (("NIGHTSHIFT_CTL_GH", self.gh_path),
                           ("GH_TOKEN", None), ("GITHUB_TOKEN", None)):
            self._saved_env[key] = os.environ.get(key)
            # Clearing the real credentials matters: if the fake is ever
            # bypassed we want a hard failure, not a silent fall-through to
            # the live API against a real repo.
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ---------------------------------------------------------------- fake

    def _write_gh(self):
        with open(self.gh_path, "w") as handle:
            handle.write(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "STATE = %r\n"
                "state = json.load(open(STATE))\n"
                "argv = sys.argv[1:]\n"
                "open(%r, 'a').write(json.dumps(argv) + '\\n')\n"
                "verb = ' '.join(argv[:2])\n"
                "if state.get('unreachable'):\n"
                "    sys.stderr.write('dial tcp: network is unreachable\\n')\n"
                "    sys.exit(1)\n"
                "if verb == 'issue view':\n"
                "    issue = state['issues'].get(argv[2])\n"
                "    if issue is None:\n"
                "        sys.stderr.write(\n"
                "            'could not resolve to an Issue with the number '\n"
                "            'of %%s.\\n' %% argv[2])\n"
                "        sys.exit(1)\n"
                "    body = dict(issue)\n"
                "    body['number'] = int(argv[2])\n"
                "    print(json.dumps(body))\n"
                "    sys.exit(0)\n"
                "if verb in ('issue comment', 'issue close'):\n"
                "    if state.get('write_fails'):\n"
                "        sys.stderr.write('502 Bad Gateway\\n')\n"
                "        sys.exit(1)\n"
                "    sys.exit(0)\n"
                "if verb == 'issue list':\n"
                "    print(json.dumps(state.get('closed_list', [])))\n"
                "    sys.exit(0)\n"
                "sys.exit(0)\n"
                % (self.state_path, self.argv_log))
        os.chmod(self.gh_path, 0o755)

    def set_issues(self, issues, **flags):
        state = {"issues": issues}
        state.update(flags)
        with open(self.state_path, "w") as handle:
            json.dump(state, handle)

    def gh_calls(self):
        if not os.path.exists(self.argv_log):
            return []
        with open(self.argv_log) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def assert_no_github_write(self):
        """A refusal must not touch GitHub AT ALL beyond its read.

        This is the assertion that makes every refusal test mean something.
        Exit 3 with the issue already closed would satisfy a code check and
        still be the exact catastrophe #424 is about.
        """
        writes = [c for c in self.gh_calls()
                  if c[:2] in (["issue", "comment"], ["issue", "close"])]
        self.assertEqual(
            writes, [],
            "a refused close performed a GitHub WRITE: %r" % (writes,))

    # ------------------------------------------------------------- helpers

    def close(self, issue, disposition, actor="crew_alpha", evidence=None,
              extra=()):
        argv = ["issue", "close", "--issue", str(issue),
                "--disposition", disposition, "--actor", actor]
        if evidence is not None:
            argv += ["--evidence", evidence]
        argv += list(extra)
        return self.c.run(*argv)

    def rows(self):
        with sqlite3.connect(self.c.db) as cx:
            cx.row_factory = sqlite3.Row
            return [dict(r) for r in cx.execute(
                "SELECT * FROM issue_close_dispositions ORDER BY id")]


# --------------------------------------------------------------------- AC-1

class DispositionGrammar(GatewayCase):
    """AC-1 — exactly one machine-parseable disposition, or no close."""

    ISSUES = {"500": OPEN_PLAIN, "600": OPEN_PLAIN}

    def test_delivered_is_accepted_and_recorded(self):
        code, body = self.close(500, "delivered")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["closed"])
        self.assertEqual(body["disposition"], "delivered")
        row, = self.rows()
        self.assertEqual(row["disposition"], "delivered")
        self.assertEqual(row["state_reason"], "COMPLETED")

    def test_the_cli_composes_the_comment_so_it_cannot_disagree_with_the_row(self):
        """The posted body and the stored body come from ONE value.

        This is the structural reason AC-1 holds: the operator hands over a
        disposition, never a comment, so there is no second place for the
        text to drift out of step with the record.
        """
        code, body = self.close(500, "delivered")
        self.assertEqual(code, 0, body)
        row, = self.rows()
        self.assertIn("Disposition: delivered", row["comment_body"])
        self.assertEqual(row["comment_body"], body["comment_body"])

        posted = [c for c in self.gh_calls() if c[:2] == ["issue", "comment"]]
        self.assertEqual(len(posted), 1, self.gh_calls())
        self.assertEqual(posted[0][-1], row["comment_body"],
                         "the body posted to GitHub is not the body recorded")

    def test_empty_disposition_is_refused_as_missing(self):
        code, body = self.close(500, "   ")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "disposition_missing")
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_prose_disposition_is_refused_as_unparseable(self):
        code, body = self.close(500, "closing this, it is basically done")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "disposition_unparseable")
        self.assertIn("Disposition: delivered", body["legal_forms"])
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_two_dispositions_are_refused_as_ambiguous(self):
        """AC-1 says EXACTLY one. Two is not "close enough"."""
        code, body = self.close(
            500, "Disposition: delivered\nDisposition: deferred-to M4")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "disposition_ambiguous")
        self.assertEqual(len(body["found"]), 2)
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_grammar_is_case_sensitive(self):
        code, body = self.close(500, "DELIVERED")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "disposition_unparseable")
        self.assert_no_github_write()


# --------------------------------------------------------------------- AC-2

class SupersededByMustExistAndBeOpen(GatewayCase):
    """AC-2 — `superseded-by #N` is checked LIVE, at the time of the close.

    Each rejection path gets its own test and its own positive counterpart,
    per AC-8. The positive case here is the discriminator: it proves the
    refusals below come from the state of issue N and not from the command
    simply being unable to supersede anything at all.
    """

    ISSUES = {
        "500": OPEN_PLAIN,
        "236": OPEN_PLAIN,          # a real, open successor
        "13": {"state": "CLOSED", "labels": []},
    }

    def test_positive_supersede_by_an_open_issue_is_accepted(self):
        code, body = self.close(500, "superseded-by #236")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["closed"])
        self.assertEqual(body["superseded_by"], 236)
        row, = self.rows()
        self.assertEqual(row["disposition"], "superseded")
        self.assertEqual(row["superseded_by"], 236)
        self.assertIsNone(row["deferred_to"])

    def test_reject_supersede_target_missing(self):
        code, body = self.close(500, "superseded-by #999999")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "supersede_target_missing")
        self.assertEqual(body["superseded_by"], 999999)
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_reject_supersede_target_closed(self):
        """The #13 -> #236 shape, backwards.

        Folding live work into an ALREADY-CLOSED issue is how a defect
        disappears: #13 was closed NOT_PLANNED and had to be re-filed as
        #236 three days later. A close naming a closed issue is refused.
        """
        code, body = self.close(500, "superseded-by #13")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "supersede_target_closed")
        self.assertEqual(body["target_state"], "CLOSED")
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_reject_supersede_self(self):
        code, body = self.close(500, "superseded-by #500")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "supersede_self")
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_the_open_check_is_live_not_cached(self):
        """The same target, accepted then refused, with only GitHub changed.

        Nothing in the control plane differs between the two halves — only
        the fake API's answer does. An implementation that cached the first
        answer would accept both and fail here. This is the discriminating
        experiment for ADR-0055's rejection of option E.
        """
        code, _ = self.close(500, "superseded-by #236")
        self.assertEqual(code, 0)

        self.set_issues({"501": OPEN_PLAIN,
                         "236": {"state": "CLOSED", "labels": []}})
        code, body = self.close(501, "superseded-by #236")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "supersede_target_closed")


# --------------------------------------------------------------------- AC-3

class DeferredToMilestone(GatewayCase):
    """AC-3 — `deferred-to <milestone>` names a milestone that exists."""

    ISSUES = {"500": OPEN_PLAIN}

    def test_positive_known_milestone_is_accepted(self):
        code, body = self.close(500, "deferred-to M4")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["deferred_to"], "M4")
        row, = self.rows()
        self.assertEqual(row["disposition"], "deferred")
        self.assertEqual(row["deferred_to"], "M4")
        self.assertIsNone(row["superseded_by"])

    def test_reject_unknown_milestone_and_name_the_ones_that_exist(self):
        code, body = self.close(500, "deferred-to M9")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "milestone_unknown")
        # Naming the real milestones is the difference between a refusal a
        # caller can act on and one they have to go and research.
        self.assertEqual(body["known_milestones"], ["M1", "M4"])
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_reject_when_the_milestone_registry_is_unreadable(self):
        os.remove(os.path.join(self.c.dir, "docs", "prd", "MILESTONES.md"))
        code, body = self.close(500, "deferred-to M4")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "milestone_registry_unreadable")
        self.assert_no_github_write()


# --------------------------------------------------------------------- AC-4

class DeferredWorkStaysInTheQueue(GatewayCase):
    """AC-4 — a deferred issue is NOT reported closable.

    Deferred work that vanishes from the queue scan is half of what the
    2026-08-26 batch actually did: #19 was honestly worded "parked, not
    dropped", and then nothing ever looked at it again.
    """

    ISSUES = {"500": OPEN_PLAIN, "501": OPEN_PLAIN}

    def _integrated_task(self, task_id, issue):
        code, body = self.c.run(
            "task", "upsert", "--id", task_id, "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", task_id, "--issue", str(issue),
            "--state", "integrated", "--size", "S")
        self.assertEqual(code, 0, body)

    def test_an_issue_with_integrated_tasks_is_closable_until_it_is_deferred(self):
        """The pair, in one test, because the transition IS the criterion."""
        self._integrated_task("t-500", 500)
        self._integrated_task("t-501", 501)

        code, body = self.c.run("issue", "closable")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["closable"], [500, 501],
                         "premise: both issues are closable before any close")

        code, body = self.close(501, "deferred-to M4")
        self.assertEqual(code, 0, body)

        code, body = self.c.run("issue", "closable")
        self.assertEqual(code, 0, body)
        self.assertEqual(
            body["closable"], [500],
            "a deferred issue must stay in the queue scan, not be reported "
            "closable (AC-4)")

    def test_issue_status_says_why_a_deferred_issue_is_not_closable(self):
        self._integrated_task("t-501", 501)
        self.assertEqual(self.close(501, "deferred-to M4")[0], 0)

        code, body = self.c.run("issue", "status", "--issue", "501")
        self.assertEqual(code, 3, body)
        self.assertFalse(body["closable"])
        self.assertEqual(body["reason"], "deferred-stays-in-queue")
        self.assertEqual(body["deferred_to"], "M4")

    def test_a_delivered_close_does_not_remove_an_issue_from_the_scan(self):
        """Discriminator: only `deferred` triggers the exclusion.

        Without this, an implementation that excluded EVERY dispositioned
        issue would pass the test above and be wrong.
        """
        self._integrated_task("t-500", 500)
        self.assertEqual(self.close(500, "delivered")[0], 0)
        code, body = self.c.run("issue", "closable")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["closable"], [500])


# --------------------------------------------------------------------- AC-5

class SensitiveIssuesRequireEvidence(GatewayCase):
    """AC-5 — a P0 or `security` issue cannot be closed on an assertion.

    #158 (P0) was closed with no closing comment at all. #129 (P0 security)
    was closed while its own fix PR was still open and self-described as
    "change 1 of 2". This criterion is the direct answer to both.
    """

    ISSUES = {
        "500": OPEN_PLAIN,
        "158": OPEN_P0,
        "129": OPEN_SECURITY,
        "236": OPEN_PLAIN,
    }

    def test_positive_p0_with_a_commit_oid_is_accepted(self):
        code, body = self.close(158, "delivered", evidence=GOOD_OID)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["sensitive"])
        row, = self.rows()
        self.assertEqual(row["sensitive"], 1)
        self.assertEqual(row["evidence"], GOOD_OID)
        self.assertIn("Evidence: " + GOOD_OID, row["comment_body"])

    def test_positive_security_with_a_pr_reference_is_accepted(self):
        code, body = self.close(129, "delivered", evidence="#134")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["sensitive"])

    def test_positive_a_test_id_is_evidence(self):
        code, body = self.close(158, "delivered", evidence="test:RT-66-BF-3")
        self.assertEqual(code, 0, body)

    def test_reject_p0_close_with_no_evidence(self):
        code, body = self.close(158, "delivered")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "evidence_required")
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_reject_security_close_with_no_evidence(self):
        code, body = self.close(129, "delivered")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "evidence_required")
        self.assert_no_github_write()

    def test_reject_prose_masquerading_as_evidence(self):
        """"we checked and it's fine" is not evidence.

        AC-5 asks for "a commit SHA, a PR number, or a test id" — something
        a reader can go and verify. Accepting free text would make the
        criterion satisfiable by typing.
        """
        code, body = self.close(158, "delivered",
                                evidence="we verified it manually")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "evidence_unparseable")
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_a_plain_issue_needs_no_evidence(self):
        """The other half of the pair: the rule fires ONLY on the sensitive
        set, so it does not become a tax on every ordinary close."""
        code, body = self.close(500, "delivered")
        self.assertEqual(code, 0, body)
        self.assertFalse(body["sensitive"])
        row, = self.rows()
        self.assertEqual(row["sensitive"], 0)
        self.assertIsNone(row["evidence"])

    def test_label_matching_is_case_insensitive(self):
        """A `p0` label must gate exactly as `P0` does — fail closed."""
        self.set_issues({"777": {"state": "OPEN",
                                 "labels": [{"name": "p0"}]}})
        code, body = self.close(777, "delivered")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "evidence_required")


# --------------------------------------------------------------------- AC-6

class BulkCloseIsRefused(GatewayCase):
    """AC-6 — more than 3 closes by one actor in any 60s window is refused.

    The whole suite exists because of one 60-second window. Virtual time is
    what makes this provable in microseconds instead of a minute, and
    deterministically rather than racily.
    """

    ISSUES = {str(n): OPEN_PLAIN for n in range(600, 640)}

    def test_positive_three_closes_in_the_window_are_accepted(self):
        for n in (600, 601, 602):
            code, body = self.close(n, "delivered", actor="heartbeat")
            self.assertEqual(code, 0, body)
        self.assertEqual(len(self.rows()), 3)

    def test_reject_the_fourth_close_in_the_window(self):
        for n in (600, 601, 602):
            self.assertEqual(self.close(n, "delivered", actor="heartbeat")[0], 0)

        code, body = self.close(603, "delivered", actor="heartbeat")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "bulk_close_refused")
        # AC-6 requires the limit and the window be NAMED in the refusal.
        self.assertEqual(body["limit"], 3)
        self.assertEqual(body["window_seconds"], 60)
        self.assertEqual(body["actor"], "heartbeat")
        self.assertEqual(len(body["recent"]), 3)
        # The refused close left no trace and touched nothing.
        self.assertEqual(len(self.rows()), 3)
        writes = [c for c in self.gh_calls() if c[:2] == ["issue", "close"]]
        self.assertEqual(len(writes), 3,
                         "the 4th close reached GitHub despite being refused")

    def test_the_window_slides_so_a_paced_actor_is_never_blocked(self):
        """The counterpart that keeps AC-6 a rate limit, not a quota.

        Refusing the 4th close FOREVER would make the gateway unusable; the
        limit is about batches, not about volume.
        """
        for n in (600, 601, 602):
            self.assertEqual(self.close(n, "delivered", actor="heartbeat")[0], 0)
        self.assertEqual(self.close(603, "delivered", actor="heartbeat")[0], 3)

        self.c.advance(61)
        code, body = self.close(603, "delivered", actor="heartbeat")
        self.assertEqual(code, 0, body)

    def test_the_limit_is_per_actor(self):
        """A second actor is not punished for the first one's batch."""
        for n in (600, 601, 602):
            self.assertEqual(self.close(n, "delivered", actor="heartbeat")[0], 0)
        code, body = self.close(610, "delivered", actor="crew_alpha")
        self.assertEqual(code, 0, body)

    def test_intents_count_so_a_retry_loop_cannot_bypass_the_limit(self):
        """The subtle one, and the reason the count is over INTENTS.

        A close whose GitHub write failed still consumed a slot in the
        window. If the count were over APPLIED rows only, an actor whose
        writes were failing could hammer the API without limit — which is
        exactly the shape of a runaway batch.
        """
        self.set_issues({str(n): OPEN_PLAIN for n in range(600, 640)},
                        write_fails=True)
        for n in (600, 601, 602):
            code, _ = self.close(n, "delivered", actor="heartbeat")
            self.assertEqual(code, 1, "the write should have failed")
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["applied_at"] is None for r in rows))

        code, body = self.close(603, "delivered", actor="heartbeat")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "bulk_close_refused")


# --------------------------------------------------------------------- AC-7

class DispositionReport(GatewayCase):
    """AC-7 — the COMPLETED share is a local query, checkable on demand."""

    ISSUES = {str(n): OPEN_PLAIN for n in range(700, 720)}
    ISSUES["236"] = OPEN_PLAIN

    def test_counts_by_disposition(self):
        self.assertEqual(self.close(700, "delivered", actor="a")[0], 0)
        self.assertEqual(self.close(701, "delivered", actor="b")[0], 0)
        self.assertEqual(self.close(702, "superseded-by #236", actor="c")[0], 0)
        self.assertEqual(self.close(703, "deferred-to M4", actor="d")[0], 0)

        code, body = self.c.run("issue", "disposition-report")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["counts"],
                         {"delivered": 2, "superseded": 1, "deferred": 1})
        # AC-3: a deferred issue is NOT counted as closed.
        self.assertEqual(body["closed_total"], 3)
        self.assertEqual(body["deferred_total"], 1)

    def test_empty_range_prints_explicit_zeros_not_an_empty_body(self):
        """Failure/empty state 2, verbatim.

        "the report prints zero counts for all three dispositions rather
        than an empty body, so 'nothing closed' is distinguishable from
        'the query failed'." An empty body would satisfy neither.
        """
        code, body = self.c.run("issue", "disposition-report",
                                "--since", "1", "--until", "2")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["counts"],
                         {"delivered": 0, "superseded": 0, "deferred": 0})
        self.assertEqual(body["closed_total"], 0)
        self.assertEqual(body["deferred_total"], 0)
        self.assertTrue(body["ok"])

    def test_the_range_actually_filters(self):
        self.assertEqual(self.close(700, "delivered", actor="a")[0], 0)
        self.c.advance(1000)
        self.assertEqual(self.close(701, "delivered", actor="b")[0], 0)

        code, body = self.c.run(
            "issue", "disposition-report",
            "--since", repr(VIRTUAL_T0 + 500), "--until", repr(VIRTUAL_T0 + 5000))
        self.assertEqual(code, 0, body)
        self.assertEqual(body["counts"]["delivered"], 1)

    def test_a_failed_close_is_never_counted_as_a_delivery(self):
        """The reason ADR-0055 rejected option B (record-then-close).

        A phantom row for an issue that is still open would inflate exactly
        the number this report exists to make trustworthy.
        """
        self.set_issues({"700": OPEN_PLAIN}, write_fails=True)
        code, _ = self.close(700, "delivered", actor="a")
        self.assertEqual(code, 1)

        code, body = self.c.run("issue", "disposition-report")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["counts"]["delivered"], 0,
                         "an intent that never reached GitHub is not a close")
        self.assertEqual(body["stale_intents"], 1)


# ------------------------------------------------ failure and empty states

class GitHubUnreachable(GatewayCase):
    """Failure state 1 — a close that cannot be recorded must not happen."""

    ISSUES = {"500": OPEN_PLAIN}

    def test_unreachable_api_refuses_the_close_and_writes_no_row(self):
        self.set_issues({"500": OPEN_PLAIN}, unreachable=True)
        code, body = self.close(500, "delivered")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "gh_api_failed")
        self.assertFalse(body["closed"])
        self.assertEqual(self.rows(), [],
                         "the close was refused, so nothing may be recorded")
        self.assert_no_github_write()

    def test_a_missing_gh_binary_fails_structurally_not_as_a_traceback(self):
        os.environ["NIGHTSHIFT_CTL_GH"] = os.path.join(self.c.dir, "no-such-gh")
        code, body = self.close(500, "delivered")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "gh_binary_missing")
        self.assertEqual(self.rows(), [])

    def test_a_write_failure_leaves_a_visible_stale_intent_not_a_silent_gap(self):
        """The crash window is VISIBLE by construction.

        This is the whole argument for the two-phase ordering: the residue
        of a half-finished close is a row an operator can find, never a
        closed issue with no disposition.
        """
        self.set_issues({"500": OPEN_PLAIN}, write_fails=True)
        code, body = self.close(500, "delivered")
        self.assertEqual(code, 1, body)
        self.assertFalse(body["closed"])
        self.assertTrue(body["stale_intent"])
        row, = self.rows()
        self.assertIsNone(row["applied_at"])

        code, body = self.c.run("issue", "undisposed")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["stale_intent_count"], 1)
        self.assertEqual(body["stale_intents"][0]["issue"], 500)


class UndisposedScan(GatewayCase):
    """Failure state 3 — a close made outside this gateway is FLAGGED.

    We cannot gate GitHub's own UI, and the Program's heartbeat still calls
    `gh issue close` directly today (ADR-0055 Consequences). Detection is the
    honest answer, and it only counts if it actually lists them.
    """

    ISSUES = {"500": OPEN_PLAIN}

    def test_an_issue_closed_outside_the_gateway_is_listed(self):
        self.set_issues(
            {"500": OPEN_PLAIN},
            closed_list=[{"number": 158, "closedAt": "2026-08-26T15:23:00Z",
                          "title": "irreversible-action deny floor"}])
        code, body = self.c.run("issue", "undisposed")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["undisposed_count"], 1)
        self.assertEqual(body["undisposed_closes"][0]["issue"], 158)

    def test_an_issue_closed_through_the_gateway_is_not_listed(self):
        """The discriminator. Without it the scan could be flagging
        everything and still pass the test above."""
        self.assertEqual(self.close(500, "delivered")[0], 0)
        self.set_issues(
            {"500": OPEN_PLAIN},
            closed_list=[{"number": 500, "closedAt": "2026-08-31T00:00:00Z",
                          "title": "closed properly"}])
        code, body = self.c.run("issue", "undisposed")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["undisposed_count"], 0)
        self.assertEqual(body["undisposed_closes"], [])


class StateReasonHonesty(GatewayCase):
    """The row and GitHub never disagree — including when they cannot agree.

    `gh issue close --reason` accepts only {completed|not planned} (gh
    2.45.0) and GitHub's REST state_reason enum has no `duplicate`. Rather
    than record DUPLICATE locally and write "not planned" to GitHub, the
    gateway refuses. Silently writing something other than what was recorded
    is the precise failure mode this command exists to prevent.
    """

    ISSUES = {"500": OPEN_PLAIN, "236": OPEN_PLAIN}

    def test_duplicate_is_refused_rather_than_silently_downgraded(self):
        code, body = self.close(500, "superseded-by #236",
                                extra=("--state-reason", "DUPLICATE"))
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "state_reason_unsupported")
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_the_reason_written_to_github_is_the_reason_recorded(self):
        code, body = self.close(500, "superseded-by #236")
        self.assertEqual(code, 0, body)
        row, = self.rows()
        self.assertEqual(row["state_reason"], "NOT_PLANNED")
        closes = [c for c in self.gh_calls() if c[:2] == ["issue", "close"]]
        self.assertEqual(len(closes), 1)
        self.assertIn("not planned", closes[0])


class AlreadyClosedIssue(GatewayCase):
    ISSUES = {"888": {"state": "CLOSED", "labels": []}}

    def test_closing_an_already_closed_issue_is_refused(self):
        code, body = self.close(888, "delivered")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "already_closed")
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()


class SchemaConstraints(GatewayCase):
    """The CHECK constraints make an incoherent row UNREPRESENTABLE.

    These assert the table itself, not the command, on purpose: the command
    can be bypassed by a future caller, and the constraint cannot.
    """

    ISSUES = {"500": OPEN_PLAIN}

    def _insert(self, **kw):
        row = {"issue": 500, "disposition": "delivered", "superseded_by": None,
               "deferred_to": None, "evidence": None, "sensitive": 0,
               "state_reason": "COMPLETED", "comment_body": "x",
               "actor": "a", "recorded_at": 1.0}
        row.update(kw)
        with sqlite3.connect(self.c.db) as cx:
            cx.execute(
                "INSERT INTO issue_close_dispositions("
                "issue,disposition,superseded_by,deferred_to,evidence,"
                "sensitive,state_reason,comment_body,actor,recorded_at) "
                "VALUES(:issue,:disposition,:superseded_by,:deferred_to,"
                ":evidence,:sensitive,:state_reason,:comment_body,:actor,"
                ":recorded_at)", row)

    def test_a_deferred_row_cannot_carry_a_superseded_by(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(disposition="deferred", deferred_to="M4",
                         superseded_by=236)

    def test_a_superseded_row_must_name_its_successor(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(disposition="superseded", superseded_by=None)

    def test_a_sensitive_row_cannot_exist_without_evidence(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(sensitive=1, evidence=None)

    def test_an_unknown_disposition_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert(disposition="wontfix")


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------- F-1 (cross-crew, PR #450)

class ActorCannotForgeTheArtifact(GatewayCase):
    """F-1 — `--actor` is public, permanent text, so it is validated input.

    Found by crew_beta's cross-crew review of PR #450 and corroborated
    independently by `security`. `--actor` was interpolated raw into the
    close-comment body while `--evidence` right beside it was constrained by
    EVIDENCE_RE, so an actor carrying a newline forged a second
    `Disposition:` line into a permanent GitHub artifact.

    The forgery is not cosmetic, and that is the point of this class: the
    posted body fed back through the gateway's OWN parser returns
    `disposition_ambiguous`. The command could write a comment it could not
    read. ADR-0055 §2 claims the posted comment and the stored row cannot
    disagree; that was true of the disposition and the evidence and false of
    the actor, which is exactly the kind of "invariant holds for the fields I
    remembered" gap a cold reviewer catches and the author does not.

    Reachable on EVERY close through a REQUIRED flag — not a corner case.
    """

    ISSUES = {"500": OPEN_PLAIN, "501": OPEN_PLAIN, "502": OPEN_PLAIN,
              "999": OPEN_PLAIN}

    #: The exact PoC from the fix packet.
    FORGERY = "mallory\nDisposition: superseded-by #999"

    def disposition_lines(self, body):
        return [ln for ln in body.splitlines()
                if ln.strip().startswith("Disposition:")]

    # -- the rejection paths ------------------------------------------------

    def test_reject_actor_forging_a_second_disposition_line(self):
        """The PoC itself. Refused at the door, nothing written anywhere."""
        code, body = self.close(500, "delivered", actor=self.FORGERY)
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "actor_malformed")
        self.assertEqual(self.rows(), [])
        self.assert_no_github_write()

    def test_reject_carriage_return_as_well_as_newline(self):
        """A lone \\r is a line break to plenty of renderers. Fail closed."""
        code, body = self.close(500, "delivered",
                                actor="mallory\rDisposition: delivered")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "actor_malformed")
        self.assert_no_github_write()

    def test_reject_markdown_injection_into_a_public_artifact(self):
        """Allowlist, not a \\r\\n blocklist — so backticks never land either."""
        code, body = self.close(500, "delivered",
                                actor="mallory`](http://evil)`")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "actor_malformed")
        self.assert_no_github_write()

    def test_reject_empty_actor(self):
        code, body = self.close(500, "delivered", actor="")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "actor_malformed")
        self.assert_no_github_write()

    def test_reject_unicode_homoglyph_impersonating_a_seat(self):
        """`рrogram` is Cyrillic-р + "rogram". It must not read as `program`."""
        code, body = self.close(500, "delivered", actor="рrogram")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "actor_malformed")
        self.assert_no_github_write()

    def test_the_refusal_happens_before_any_github_call_at_all(self):
        """Cheapest refusal first: a malformed actor costs no API round trip.

        Stronger than assert_no_github_write, which still tolerates the READ.
        """
        code, _ = self.close(500, "delivered", actor=self.FORGERY)
        self.assertEqual(code, 3)
        self.assertEqual(self.gh_calls(), [])

    # -- the matching positives (AC-8's pairing rule) -----------------------

    def test_positive_ordinary_seat_names_are_accepted(self):
        """The pair. A rule that refused every actor would pass every test
        above and make the gateway unusable."""
        for issue, actor in ((500, "crew_alpha"), (501, "program"),
                             (502, "crew_alpha/pm")):
            with self.subTest(actor=actor):
                code, body = self.close(issue, "delivered", actor=actor)
                self.assertEqual(code, 0, body)
        rows = self.rows()
        self.assertEqual([r["actor"] for r in rows],
                         ["crew_alpha", "program", "crew_alpha/pm"])

    def test_the_posted_artifact_carries_exactly_one_disposition_line(self):
        """The invariant F-1 actually broke, asserted on the stored artifact.

        Not "the actor is clean" but "the artifact is unambiguous" — the
        property ADR-0055 §2 claims, checked on the text that reaches GitHub.
        """
        code, _ = self.close(500, "delivered", actor="crew_alpha")
        self.assertEqual(code, 0)
        row, = self.rows()
        self.assertEqual(self.disposition_lines(row["comment_body"]),
                         ["Disposition: delivered"])

    def test_the_stored_body_is_the_body_that_was_posted(self):
        """Row and artifact still cannot disagree, now including the actor."""
        code, _ = self.close(500, "superseded-by #999", actor="crew_alpha")
        self.assertEqual(code, 0)
        row, = self.rows()
        posted = [c for c in self.gh_calls() if c[:2] == ["issue", "comment"]]
        self.assertEqual(len(posted), 1, posted)
        self.assertIn(row["comment_body"], posted[0])
        self.assertEqual(self.disposition_lines(row["comment_body"]),
                         ["Disposition: superseded-by #999"])
