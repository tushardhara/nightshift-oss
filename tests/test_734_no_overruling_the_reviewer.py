"""Issue #734 / ADR-0095, epic #716 change 6: a recorded blocker is not
weakened by re-attesting the head it was raised against.

> *"A team may not overrule the reviewer checking its work. Both teams
> downgraded findings raised against them. That is not a coverage problem and
> no amount of extra reviewing fixes it."*

**Read this before changing anything here.** The first implementation of this
change gated `review log`, and an adversarial review showed it was gating a
door that was already locked: `review log` refuses to overwrite an existing
receipt, so the downgrade it "prevented" could not happen on that path. Net
effect on the gating table: zero. Meanwhile the path that DOES work --
`review receipt-record`, whose `_write_review_findings` deletes every row for
`(task, head, domain)` and re-inserts the new payload -- was untouched, and
the review demonstrated the full attack against it at exit 0.

So the check now lives where the DELETE happens, and is keyed on what the
DELETE is about to remove. Two consequences worth keeping:

- **It needs no seat identity.** The first version asked "is the party filing
  this the crew under review?", which the attested path cannot even answer and
  which #725 says means nothing anyway. On a given head a finding never
  legitimately gets weaker: eligibility gates per head, so a real fix produces
  a new head and a fresh review. Nothing has to be cleared for the fix loop.
- **Only MERGE_BLOCKERs are protected.** Guarding every severity refused a
  reviewer revising its own review (`test_recapture_after_remint_...` drops a
  FOLLOW_UP and adds a MERGE_BLOCKER -- the record getting stronger), which is
  the false refusal that gets a gate deleted rather than argued with.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

from ctlutil import Ctl, REPO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_review_receipts import (  # noqa: E402
    FAKE_GH, HEAD, PR_URL, COMMENT_URL, block, comment_obj)


class ABlockerSurvivesReAttestation(unittest.TestCase):
    """The reviewer's own end-to-end repro, turned into the regression test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctl = Ctl()
        self.ctl.run("task", "upsert", "--id", "t1", "--crew", "crew_alpha",
                     "--agent", "qa", "--title", "smoke", "--state", "pending",
                     "--size", "S", "--pr-url", PR_URL)
        self.gh_path = os.path.join(self.tmp.name, "fake_gh.py")
        with open(self.gh_path, "w", encoding="utf-8") as handle:
            handle.write(FAKE_GH)
        os.chmod(self.gh_path, 0o755)
        self.response_path = os.path.join(self.tmp.name, "response.json")
        os.environ["NIGHTSHIFT_CTL_GH"] = self.gh_path
        os.environ["FAKE_GH_RESPONSE_FILE"] = self.response_path
        self.addCleanup(os.environ.pop, "NIGHTSHIFT_CTL_GH", None)
        self.addCleanup(os.environ.pop, "FAKE_GH_RESPONSE_FILE", None)

    # -- harness ----------------------------------------------------------

    def mint(self):
        code, body = self.ctl.run("review", "marker-mint", "--task", "t1",
                                  "--head-sha", HEAD, "--domain", "qa")
        self.assertEqual(code, 0, body)
        return body["marker"]

    def attest(self, findings, verdict="fail"):
        """Mint a fresh marker and attest a review carrying `findings`.

        Minting is unauthenticated -- anyone can mint for any task/head/domain
        -- which is exactly why re-attestation is reachable by the crew under
        review and why this test needs no forged identity to reproduce it.
        """
        marker = self.mint()
        with open(self.response_path, "w", encoding="utf-8") as handle:
            json.dump(comment_obj(block(marker=marker, verdict=verdict,
                                        findings=findings)), handle)
        return self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "qa", "--comment-url", COMMENT_URL)

    def findings_rows(self):
        with sqlite3.connect(self.ctl.db) as cx:
            cx.row_factory = sqlite3.Row
            return [(r["severity"], r["summary"]) for r in cx.execute(
                "SELECT severity, summary FROM review_findings"
                " WHERE task_id='t1' ORDER BY summary")]

    BLOCKER = {"severity": "MERGE_BLOCKER", "criterion": "AC-1",
               "summary": "the fence is inert"}

    def raise_blocker(self):
        code, body = self.attest([dict(self.BLOCKER)])
        self.assertEqual(code, 0, body)
        self.assertEqual(self.findings_rows(),
                         [("MERGE_BLOCKER", "the fence is inert")])

    # -- the attack, step by step -----------------------------------------

    def test_re_attesting_the_blocker_lower_is_refused(self):
        """Step 2 of the demonstrated attack: exit 0, table rewritten."""
        self.raise_blocker()
        code, body = self.attest(
            [dict(self.BLOCKER, severity="FOLLOW_UP")], verdict="pass")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["error"], "review_findings_weakened")
        self.assertIn("lowers it to FOLLOW_UP", " ".join(body["reasons"]))
        self.assertEqual(self.findings_rows(),
                         [("MERGE_BLOCKER", "the fence is inert")],
                         "the blocker left the table eligibility gates on")

    def test_re_attesting_with_the_blocker_omitted_is_refused(self):
        """Step 3: a clean re-attestation carrying no findings at all. This is
        the cheapest form -- it needs no disposition and no rationale."""
        self.raise_blocker()
        code, body = self.attest([], verdict="pass")
        self.assertEqual(code, 3, body)
        self.assertIn("not withdrawn by omission", " ".join(body["reasons"]))
        self.assertEqual(self.findings_rows(),
                         [("MERGE_BLOCKER", "the fence is inert")])

    def test_the_attested_path_will_not_carry_a_disposition_at_all(self):
        """Worth pinning, because it changes which bypasses exist.

        `fixed` looked like the cheapest bypass -- `_write_review_findings`
        skips `fixed` and `overturned` identically, both drop the row, and
        nothing verifies a fix happened. But the attested block's schema does
        not accept a `disposition` key, so on THIS path the move cannot even be
        expressed: it is refused as malformed before any of this change runs.

        Asserted rather than assumed, because if that validation ever widened
        to accept dispositions, the weakening check below is what would have to
        catch it -- and this test is the tripwire that says so.
        """
        self.raise_blocker()
        for disposition in ("fixed", "overturned"):
            code, body = self.attest(
                [dict(self.BLOCKER, disposition=disposition)], verdict="pass")
            self.assertEqual(code, 3, body)
            self.assertEqual(body["reason"], "findings_malformed", body)
        self.assertEqual(self.findings_rows(),
                         [("MERGE_BLOCKER", "the fence is inert")])

    def test_the_logged_path_cannot_reach_a_head_that_already_has_findings(self):
        """Why the check lives at only ONE call site.

        `_write_review_findings` has two callers, and gating both looked
        obviously right. It is not: `review_findings` rows are only ever
        written alongside a receipt, and `review log` refuses outright once a
        receipt exists -- so a logged entry can never reach a head that already
        carries findings. A gate there would be code nothing can invoke.

        Pinned because the reasoning is not visible from either call site, and
        the next reader will otherwise "fix" the asymmetry back.
        """
        self.raise_blocker()
        reviews = os.path.join(self.ctl.omni, "reviews.md")
        payload = {
            "repo": "example-org/example-service", "pr": 4211,
            "task_id": "t1", "head_sha": HEAD, "domain": "qa",
            "author_seat": "crew_alpha/eng_codex",
            "reviewer_seat": "crew_alpha/eng_claude",
            "vendor": "Claude", "verdict": "pass", "loops": 1,
            "independent": "no", "slot_state": "absent",
            "slot_absence_reason": "outside the semaphore",
            "provenance": "local", "recorded_at": "2026-09-07T12:00:00Z",
            "findings": [dict(self.BLOCKER, disposition="fixed")],
        }
        code, body = self.ctl.run("review", "log", "--report-json", "-",
                                  "--reviews-path", reviews,
                                  stdin=json.dumps(payload))
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"], body)
        self.assertTrue(
            body["receipt_skipped_reason"].startswith("existing_receipt_from"),
            body)
        self.assertEqual(self.findings_rows(),
                         [("MERGE_BLOCKER", "the fence is inert")])

    def test_the_receipt_is_not_written_either(self):
        """Refused before the upsert, so a rejected re-attestation leaves the
        receipt as well as the findings untouched -- otherwise the verdict
        would flip to pass while the blocker stayed."""
        self.raise_blocker()
        self.attest([], verdict="pass")
        with sqlite3.connect(self.ctl.db) as cx:
            verdicts = [r[0] for r in cx.execute(
                "SELECT verdict FROM review_receipts WHERE task_id='t1'")]
        self.assertEqual(verdicts, ["fail"])

    # -- what must keep working -------------------------------------------

    def test_a_fix_clears_it_at_a_new_head(self):
        """The load-bearing negative control. The rule is only defensible if
        the fix loop still works -- and it does, because eligibility gates per
        head: the blocker was raised against THIS head, and the fix produces
        another one."""
        self.raise_blocker()
        other = "e" * 40
        code, body = self.ctl.run("review", "marker-mint", "--task", "t1",
                                  "--head-sha", other, "--domain", "qa")
        self.assertEqual(code, 0, body)
        with open(self.response_path, "w", encoding="utf-8") as handle:
            json.dump(comment_obj(block(marker=body["marker"], head_sha=other,
                                        verdict="pass", findings=[])), handle)
        code, body = self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", other,
            "--domain", "qa", "--comment-url", COMMENT_URL)
        self.assertEqual(code, 0, body)
        with sqlite3.connect(self.ctl.db) as cx:
            blocking = list(cx.execute(
                "SELECT 1 FROM review_findings WHERE task_id='t1'"
                " AND head_sha=? AND severity='MERGE_BLOCKER'", (other,)))
        self.assertEqual(blocking, [], "the new head still carries the blocker")

    def test_re_attesting_the_same_blocker_unchanged_is_fine(self):
        """A reviewer re-posting its own review must not be refused."""
        self.raise_blocker()
        code, body = self.attest([dict(self.BLOCKER)])
        self.assertEqual(code, 0, body)

    def test_raising_the_severity_is_fine(self):
        self.raise_blocker()
        code, body = self.attest([
            dict(self.BLOCKER),
            {"severity": "MERGE_BLOCKER", "criterion": "AC-2",
             "summary": "and another"}])
        self.assertEqual(code, 0, body)
        self.assertEqual(len(self.findings_rows()), 2)

    def test_a_lesser_finding_may_still_be_revised_away(self):
        """Only blockers are protected. Guarding every severity refused a
        reviewer revising its own review, which is legitimate and covered by
        test_recapture_after_remint_replaces_the_receipt_and_its_findings."""
        code, body = self.attest([
            {"severity": "FOLLOW_UP", "criterion": "c", "summary": "old"}])
        self.assertEqual(code, 0, body)
        code, body = self.attest([
            {"severity": "MERGE_BLOCKER", "criterion": "c", "summary": "new"}])
        self.assertEqual(code, 0, body)
        self.assertEqual(self.findings_rows(), [("MERGE_BLOCKER", "new")])

    def test_a_different_domains_blocker_is_not_confused_with_this_one(self):
        """The rows are scoped by (task, head, domain), so an identically
        worded finding in another domain is a different finding. The first
        implementation keyed on a fingerprint that excluded all three and
        refused unrelated work across tasks."""
        self.raise_blocker()
        code, body = self.ctl.run("review", "marker-mint", "--task", "t1",
                                  "--head-sha", HEAD, "--domain", "security")
        self.assertEqual(code, 0, body)
        with open(self.response_path, "w", encoding="utf-8") as handle:
            json.dump(comment_obj(block(marker=body["marker"], domain="security",
                                        verdict="pass", findings=[])), handle)
        code, body = self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "security", "--comment-url", COMMENT_URL)
        self.assertEqual(code, 0, body)


if __name__ == "__main__":
    unittest.main()
