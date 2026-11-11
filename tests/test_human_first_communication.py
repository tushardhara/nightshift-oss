"""ADR-0027: human-facing records lead with one bounded plain-language summary."""

import os
import re
import unittest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRST_SCREEN = (
    "Status",
    "What changed",
    "Live impact",
    "Still not done",
    "Next step",
    "Owner action",
)
STATUSES = (
    "On track",
    "Blocked",
    "Ready for review",
    "Ready to merge",
    "Merged",
    "Superseded",
    "Done",
    "Closed without completion",
)
ISSUE_TEMPLATES = (
    ".github/ISSUE_TEMPLATE/backlog-item.md",
    ".github/ISSUE_TEMPLATE/incident.md",
    ".github/ISSUE_TEMPLATE/owner-request.md",
)
FANOUT_SKILLS = (
    "program/agents/crew_alpha/skills/fanout/SKILL.md",
    "program/agents/crew_beta/skills/fanout/SKILL.md",
)


def read(relpath):
    with open(os.path.join(REPO, relpath), encoding="utf-8") as source:
        return source.read()


def h2_headings(body):
    return re.findall(r"^## (.+)$", body, re.MULTILINE)


def section(body, start, end):
    return body.split(start, 1)[1].split(end, 1)[0]


def inline_code_values(body):
    return tuple(re.findall(r"`([^`]+)`", body))


def word_count(body):
    return len(re.findall(r"\b[\w'-]+\b", body))


class HumanFirstCommunicationTest(unittest.TestCase):
    def test_contract_names_audience_bound_and_required_first_screen(self):
        body = read("docs/HUMAN-COMMUNICATION.md")
        for word in ("owner", "operator", "contributor", "30-second", "180 words", "plain words"):
            self.assertIn(word, body)
        headings = h2_headings(body)
        self.assertEqual(headings[:6], list(FIRST_SCREEN))
        self.assertGreater(headings.index("Detailed evidence"), headings.index("Owner action"))
        self.assertEqual(
            inline_code_values(section(body, "## Status", "## What changed")),
            STATUSES,
        )
        self.assertIn("live-system impact and live-data impact", body)
        self.assertIn("new issue body is the intake exception", body)
        self.assertIn("Every later\nstatus comment", body)
        for example in ("Good issue update", "Good PR description", "Good status report"):
            self.assertIn(example, headings)
            example_body = section(body, "## " + example, "\n## ")
            self.assertGreater(word_count(example_body), 0)
            self.assertLessEqual(word_count(example_body), 180)
        self.assertIn("Bad examples", headings)

    def test_pr_template_leads_with_same_bounded_summary(self):
        body = read(".github/pull_request_template.md")
        self.assertEqual(h2_headings(body)[:6], list(FIRST_SCREEN))
        self.assertIn("180 words", body)
        self.assertIn("30-second summary", body)
        self.assertGreater(body.index("## Detailed evidence"), body.index("## Owner action"))
        self.assertEqual(
            inline_code_values(section(body, "## Status", "## What changed")),
            STATUSES,
        )
        self.assertIn("live systems and live data", body)

    def test_issue_templates_put_plain_problem_fields_first(self):
        expected = ["Problem", "Impact", "Done when", "Not included"]
        for path in ISSUE_TEMPLATES:
            with self.subTest(path=path):
                self.assertEqual(h2_headings(read(path))[:4], expected)

    def test_tracker_requires_first_screen_before_internal_evidence(self):
        body = read("program/skills/tracker/SKILL.md")
        self.assertLess(body.index("## Human-facing updates"), body.index("## Board stages"))
        self.assertIn("every owner-facing status comment on\nan issue or PR", body)
        for field in ("Problem", "Impact", "Done\nwhen", "Not included"):
            self.assertIn(field, section(body, "## Human-facing updates", "## Board stages"))
        reporting = body.split("## Reporting to the owner", 1)[1]
        for required in (*FIRST_SCREEN, "30-second summary", "180 words", "plain words"):
            self.assertIn(required, reporting)
        self.assertRegex(reporting, r"Do not put internal IDs, hashes")
        self.assertLess(reporting.index("Owner action"), reporting.index("Detailed evidence"))
        self.assertEqual(
            inline_code_values(section(reporting, "1. **Status**", "2. **What changed**")),
            STATUSES,
        )
        self.assertIn("live-system and live-data impact", reporting)
        self.assertIn("nightshift-ctl pr-create", body)
        self.assertIn("human-first-pr-body.md", body)
        self.assertIn("human-first-done-comment.md", body)
        self.assertNotIn('gh pr create --body "Refs', body)
        self.assertNotIn("gh issue close <n> --comment", body)

    def test_bootstrap_embeds_current_intake_and_status_contracts(self):
        body = read("bin/nightshift-tracker-init.sh")
        for name in ("Backlog item", "Incident", "Owner request"):
            template = section(body, "name: " + name, "\nEOF")
            self.assertEqual(h2_headings(template)[:4], [
                "Problem", "Impact", "Done when", "Not included"
            ])
        status_body = section(body, "<<'BODYEOF'", "\nBODYEOF")
        status_body_flat = re.sub(r"\s+", " ", status_body)
        for required in (*FIRST_SCREEN, *STATUSES, "180 words", "live systems", "live data"):
            self.assertIn(required, status_body_flat)
        self.assertLess(status_body.index("Owner action"), status_body.index("Detailed evidence"))

    def test_agent_dispatch_contract_requires_human_first_pr_body(self):
        for path in FANOUT_SKILLS:
            with self.subTest(path=path):
                body = read(path)
                self.assertIn("six-field summary", body)
                self.assertIn("docs/HUMAN-COMMUNICATION.md", body)
                self.assertLess(body.index("six-field summary"), body.index("technical evidence"))

    def test_adr_keeps_history_and_evidence_but_records_reversal(self):
        body = read("docs/decisions/ADR-0027-human-first-layered-communication.md")
        for required in (
            "Status: proposed",
            "Rewrite old issue and PR history",
            "Remove technical evidence",
            "Layer a short human summary",
            "New issue templates lead with the intake fields",
            "every owner-facing\nissue comment, PR comment, and status comment",
            "## Reversal trigger",
        ):
            self.assertIn(required, body)


if __name__ == "__main__":
    unittest.main()
