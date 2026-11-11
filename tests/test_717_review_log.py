"""Issue #717 / ADR-0090: review findings are recorded, and the retro can
compute an escaped-defect count from them.

Before this change `.omni/reviews.md` was written by NOTHING. `nightshift-ctl`
never touched it, no policy did, and the only instruction to append a line
lived in skill prose. A 12-hour run containing two genuine cross-crew reviews
left the file byte-identical, `review_receipts` held 0 rows after months of
operation, and an org redesign was drafted on that empty reading.

**What this file learned the hard way.** Its first version had 49 tests, all
green, and a mutation pass killed only 50 of 61 injected defects. The eleven
survivors were not obscure: `>` becoming `>=`, the ordering comparison deleted
outright, the earliest pass becoming the latest, and the renderer dropping the
machine-readable fence entirely. Every one of those changes the number the
whole instrument exists to produce. They survived because
`test_a_blocker_found_before_any_pass_has_not_escaped` had **no pass in its
fixture**, so the branch it named never executed, and because every file
assertion was satisfied by the JSON fence alone. So:

- Ordering tests carry a real pass and assert BOTH directions.
- Fixtures go through `render_entry` -> `parse_log`, the production path, so
  the ordinal and the fence are exercised rather than bypassed.
- The prose half of the file is asserted, not just the fence.
- AC-1 says "shown by diffing the file". `DiffOfAPreExistingFile` diffs a file
  that already had content, because the previous "diff" compared against `""`
  and reduced to `after != ""`.

13 tests here go red against the base revision under
`NIGHTSHIFT_TEST_CTL=<base>/bin/nightshift-ctl` (WA-008: a regression test
nobody can watch fail is not evidence): every test in `ReviewLogCliTest` and
`ReceiptRecordWritesTheLogTest`. The skill-prose and header tests read `REPO`
and cannot flip under that procedure -- they guard against drift, they are not
base-revision evidence, and they are not counted among the 13.
"""

import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from ctlutil import CTL, REPO, Ctl

sys.path.insert(0, os.path.join(REPO, "bin"))
import nightshift_review_log as review_log  # noqa: E402
import nightshift_review_receipts as receipts  # noqa: E402


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
PR_URL = "https://github.com/example-org/example-service/pull/4211"
OTHER_PR_URL = "https://github.com/example-org/example-service/pull/4212"
COMMENT_URL = PR_URL + "#issuecomment-555"
CONTRACT = os.path.join(
    REPO, "docs/reviews/231-persist-review-receipts-CONTRACT.md")

BLOCKER = {"severity": "MERGE_BLOCKER", "criterion": "AC-3",
           "location": "gate/permit.go:228-252",
           "summary": "role demotion between validation and execution"}


def report(**overrides):
    """A well-formed review report; every test perturbs exactly one thing."""
    base = {
        "repo": "example-org/example-service",
        "pr": 4211,
        "task_id": "t1",
        "head_sha": HEAD,
        "domain": "cross_crew_review",
        "author_seat": "crew_alpha/eng_codex",
        "reviewer_seat": "crew_beta/beta_reviewer_claude",
        "vendor": "Claude",
        "verdict": "fail",
        "loops": 1,
        "independent": "yes",
        "slot_state": "absent",
        "slot_absence_reason": "reviewer dispatched outside the semaphore",
        "provenance": "local",
        "recorded_at": "2026-09-06T10:26:50Z",
        "findings": [dict(BLOCKER)],
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def passing(**overrides):
    fields = {"verdict": "pass", "findings": []}
    fields.update(overrides)
    return report(**fields)


def log_of(*reports):
    """Build a real log and read it back -- the production path.

    Rendering and re-parsing is what assigns the ordinal and exercises the
    fence. Handing hand-built dicts straight to `escaped_defects` skipped
    both, which is why the renderer could drop the fence without a test
    noticing.
    """
    text = "".join(
        review_log.render_entry(review_log.validate_entry(r)) for r in reports)
    return review_log.parse_log(text)


def escapes(*reports, **kwargs):
    return review_log.escaped_defects(log_of(*reports)["entries"], **kwargs)


class EntryValidationTest(unittest.TestCase):
    """One assertion per refusal branch. Every one of these can fail."""

    def test_a_rendered_entry_survives_a_write_read_write_cycle(self):
        """Byte-level, not dict-level. The previous version compared
        `validate(parse(render(x)))` to `x`, which is close to a tautology
        because the fence body IS `canonical_json(entry)`. Re-rendering what
        was parsed and comparing BYTES catches a renderer that drops a field
        the fence still carries."""
        entry = review_log.validate_entry(report())
        first = review_log.render_entry(entry)
        parsed = review_log.parse_log(first)["entries"]
        self.assertEqual(len(parsed), 1)
        self.assertEqual(review_log.render_entry(parsed[0]), first)

    def test_editing_a_rendered_entry_is_caught_not_scored(self):
        text = review_log.render_entry(review_log.validate_entry(report()))
        tampered = text.replace('"MERGE_BLOCKER"', '"FOLLOW_UP"')
        result = review_log.parse_log(tampered)
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["unreadable"],
                         [{"index": 0, "reason": "id_mismatch"}])

    def test_deleting_a_fence_is_reported_not_silently_rescored(self):
        """The header promises an edited entry is "refused on read rather
        than silently rescored". That was true for MODIFICATION and false for
        DELETION: removing a fence simply made the entry disappear with exit
        0 and a smaller number."""
        text = "".join(review_log.render_entry(review_log.validate_entry(r))
                       for r in (passing(), report()))
        cut = text[:text.rindex("```nightshift-review-log")]
        result = review_log.parse_log(cut)
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["headings"] - result["fences"], 1,
                         "an entry heading with no fence must be countable")

    def test_a_truncated_trailing_fence_is_reported(self):
        text = review_log.render_entry(review_log.validate_entry(report()))
        result = review_log.parse_log(text[:-40])
        self.assertEqual(result["entries"], [])
        self.assertTrue(
            any(u["reason"] == "unterminated_fence"
                for u in result["unreadable"]), result["unreadable"])

    def test_an_identical_entry_appended_twice_counts_once(self):
        """`receipt-record` upserts its row but appends unconditionally, so a
        retry after a transient failure doubled every number the retro
        quotes."""
        one = review_log.render_entry(review_log.validate_entry(report()))
        result = review_log.parse_log(one + one)
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["duplicates"], 1)

    def test_a_review_with_no_slot_records_and_names_the_condition(self):
        entry = review_log.validate_entry(report())
        self.assertEqual(entry["slot_state"], "absent")
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(slot_absence_reason=None))
        self.assertEqual(caught.exception.reason,
                         "slot_absence_reason_missing")

    def test_independence_cannot_be_claimed_by_a_seat_reading_its_own_work(self):
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(
                report(reviewer_seat="crew_alpha/eng_codex"))
        self.assertEqual(caught.exception.reason, "independence_contradicted")

    def test_a_self_review_is_recorded_with_the_contradiction_named(self):
        entry = review_log.validate_entry(
            report(reviewer_seat="crew_alpha/eng_codex", independent="no"))
        self.assertEqual(entry["contradictions"], ["self_review"])

    def test_seats_may_be_omitted_only_by_an_entry_that_admits_ignorance(self):
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(
                report(author_seat=None, reviewer_seat=None))
        self.assertEqual(caught.exception.reason, "seat_missing")
        entry = review_log.validate_entry(report(
            author_seat=None, reviewer_seat=None, independent="unknown"))
        self.assertNotIn("author_seat", entry)
        self.assertNotIn("reviewer_seat", entry)

    def test_an_attestation_cannot_be_declared_into_existence(self):
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(provenance="attested"))
        self.assertEqual(caught.exception.reason, "attestation_incomplete")
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(comment_url=COMMENT_URL))
        self.assertEqual(caught.exception.reason, "attestation_unearned")

    def test_a_pass_carrying_an_unresolved_blocker_is_a_contradiction(self):
        self.assertEqual(
            review_log.validate_entry(report(verdict="pass"))["contradictions"],
            ["pass_with_merge_blocker"])

    def test_a_pass_whose_blocker_is_already_fixed_is_not_a_contradiction(self):
        """The ADR says "unresolved". The check tested severity alone, so a
        pass whose blocker was fixed was flagged like a rubber stamp."""
        entry = review_log.validate_entry(report(verdict="pass", findings=[
            dict(BLOCKER, disposition="fixed")]))
        self.assertEqual(entry["contradictions"], [])

    def test_the_domain_vocabulary_is_adr_0039s_and_not_a_second_copy(self):
        for domain in receipts.CANONICAL_DOMAINS:
            self.assertEqual(
                review_log.validate_entry(report(domain=domain))["domain"],
                domain)
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(domain="vibes"))
        self.assertEqual(caught.exception.reason, "domain_unknown")

    def test_a_review_kind_the_vocabulary_omits_is_recordable_on_purpose(self):
        entry = review_log.validate_entry(report(
            domain="rollout_sequence", domain_outside_vocabulary=True))
        self.assertTrue(entry["domain_outside_vocabulary"])
        self.assertIn("outside the frozen review vocabulary",
                      review_log.render_entry(entry))

    def test_a_mistyped_domain_still_fails_loudly(self):
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(domain="cross_crew_reveiw"))
        self.assertEqual(caught.exception.reason, "domain_unknown")
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(
                report(domain="qa", domain_outside_vocabulary=True))
        self.assertEqual(caught.exception.reason,
                         "domain_outside_vocabulary_contradicted")

    def test_promoting_a_domain_does_not_brick_entries_already_logged(self):
        """The read path must not run write-time guards. There is an open PR
        adding an eighth canonical domain; the moment it lands, every entry
        already logged under the escape hatch would start refusing -- and take
        the whole file with it, permanently, because an entry cannot be edited
        without breaking its own digest."""
        entry = review_log.validate_entry(report(
            domain="rollout_sequence", domain_outside_vocabulary=True))
        text = review_log.render_entry(entry)
        promoted = json.loads(review_log.BLOCK_FENCE.findall(text)[0])
        promoted.pop("entry_id")
        with self.assertRaises(review_log.LogDenied):
            review_log.validate_entry(dict(promoted, domain="qa"), strict=True)
        review_log.validate_entry(dict(promoted, domain="qa"), strict=False)

    def test_only_a_reconstruction_may_say_its_head_is_unrecoverable(self):
        entry = review_log.validate_entry(
            report(provenance="reconstructed", head_sha="unrecoverable"))
        self.assertEqual(entry["head_sha"], "unrecoverable")
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(head_sha="unrecoverable"))
        self.assertEqual(caught.exception.reason, "head_sha_invalid")

    def test_an_abbreviated_sha_is_not_an_exact_head(self):
        """Exact-head binding is the point of the field. Widening the pattern
        to accept 7-hex prefixes survived every earlier test, because the only
        negative fixture was rejected for being non-hex, not for being
        short."""
        for short in ("3d71ecc", "a" * 39, "a" * 41):
            with self.assertRaises(review_log.LogDenied) as caught:
                review_log.validate_entry(report(head_sha=short))
            self.assertEqual(caught.exception.reason, "head_sha_invalid",
                             short)

    def test_a_timestamp_that_is_not_an_instant_is_refused(self):
        """A regex is a shape check; only the calendar knows whether a date
        exists. These all matched the shape and were stored verbatim, then
        sorted lexicographically among real instants."""
        for bad in ("2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z",
                    "2026-09-32T00:00:00Z", "2026-09-01T99:99:99Z",
                    "2026-00-00T00:00:00Z", "last tuesday"):
            with self.assertRaises(review_log.LogDenied) as caught:
                review_log.validate_entry(report(recorded_at=bad))
            self.assertEqual(caught.exception.reason, "recorded_at_invalid",
                             bad)

    def test_two_calls_a_fraction_of_a_second_apart_order_correctly(self):
        """Whole-second resolution made ordinary sequential calls collide, and
        a collision reads as "order unknown" and scores no escape."""
        first = review_log.normalize_timestamp(1788700000.0)
        second = review_log.normalize_timestamp(1788700000.2)
        self.assertNotEqual(first, second)
        self.assertLess(first, second)

    def test_one_instant_has_exactly_one_spelling(self):
        """Two spellings is worse than one coarse one. `receipt-record` takes
        whole seconds from a GitHub comment and `review log` takes fractions
        from the clock; ranks compare as STRINGS and "." sorts before "Z", so
        every mixed pair inside one second ordered BACKWARDS."""
        whole = review_log.normalize_timestamp("2026-09-07T12:00:00Z")
        later = review_log.normalize_timestamp("2026-09-07T12:00:00.5Z")
        self.assertEqual(whole, "2026-09-07T12:00:00.000Z")
        self.assertLess(whole, later, "the later instant must sort later")

    def test_a_zero_millisecond_entry_reads_back(self):
        """Emitting `.000Z` and parsing it back to a bare `Z` gave a different
        digest, so the entry read as tampered with — about one `review log`
        call in a thousand, and every one with an integer timestamp."""
        for stamp in (1789000000.0, 1789000000, "2026-09-07T12:00:00Z"):
            entry = review_log.validate_entry(report(recorded_at=stamp))
            result = review_log.parse_log(review_log.render_entry(entry))
            self.assertEqual(len(result["entries"]), 1,
                             f"{stamp!r} -> {result['unreadable']}")

    def test_a_newline_cannot_smuggle_a_forged_entry_out_of_a_finding(self):
        """`note` was quoted; `summary`, `criterion`, `disposition_note` and
        `slot_absence_reason` were rendered raw into a bullet or a table cell,
        and a newline in any of them let a whole forged entry out."""
        forged = ("real\n\n---\n\n### 2026-09-09 · `o/r` PR #1 · `security`\n"
                  "\n| verdict | **pass** |")
        for field, payload in (("summary", {"severity": "FOLLOW_UP",
                                            "summary": forged}),
                               ("criterion", {"severity": "FOLLOW_UP",
                                              "summary": "x",
                                              "criterion": forged})):
            with self.assertRaises(review_log.LogDenied) as caught:
                review_log.validate_entry(report(findings=[payload]))
            self.assertEqual(caught.exception.reason,
                             f"{field}_contains_newline")
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(slot_absence_reason=forged))
        self.assertEqual(caught.exception.reason,
                         "slot_absence_reason_contains_newline")

    def test_findings_not_recovered_is_not_the_same_as_no_findings(self):
        """A reconstruction whose findings are simply gone must not encode
        them as zero in the half the retro reads."""
        entry = review_log.validate_entry(report(
            provenance="reconstructed", findings_recovered=False, findings=[]))
        self.assertFalse(entry["findings_recovered"])
        self.assertIn("NOT RECOVERED", review_log.render_entry(entry))
        clean = review_log.validate_entry(passing())
        self.assertTrue(clean["findings_recovered"])
        self.assertIn("none found", review_log.render_entry(clean))

    def test_only_a_reconstruction_may_leave_its_findings_unrecovered(self):
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(findings_recovered=False,
                                             findings=[]))
        self.assertEqual(caught.exception.reason,
                         "findings_recovered_invalid")
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(provenance="reconstructed",
                                             findings_recovered=False))
        self.assertEqual(caught.exception.reason,
                         "findings_recovered_contradicted")

    def test_an_unknown_verdict_is_reconstruction_only(self):
        """Inventing a `pass` for a review whose verdict is not recorded
        creates the `first_pass` every escape count is measured against."""
        entry = review_log.validate_entry(
            report(provenance="reconstructed", verdict="unknown"))
        self.assertEqual(entry["verdict"], "unknown")
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(verdict="unknown"))
        self.assertEqual(caught.exception.reason, "verdict_invalid")

    def test_a_disposition_that_changes_the_count_must_carry_its_reason(self):
        for disposition in ("overturned", "upheld", "deferred"):
            with self.assertRaises(review_log.LogDenied) as caught:
                review_log.validate_entry(report(findings=[
                    dict(BLOCKER, disposition=disposition)]))
            self.assertEqual(caught.exception.reason,
                             "disposition_note_missing", disposition)

    def test_a_finding_defaults_to_open_not_to_resolved(self):
        self.assertEqual(
            review_log.validate_entry(report())["findings"][0]["disposition"],
            "open")

    def test_free_text_may_not_carry_a_code_fence(self):
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(note="see ```this```"))
        self.assertEqual(caught.exception.reason, "note_contains_code_fence")

    def test_a_field_rendered_inside_backticks_may_not_contain_one(self):
        """`location` is emitted inside a backtick span; two backticks close
        it and open a fence for a human reader."""
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(findings=[
                dict(BLOCKER, location="src/a.py``")]))
        self.assertEqual(caught.exception.reason, "location_contains_backtick")

    def test_control_characters_never_reach_the_file(self):
        for payload in ("a\x00b", "a\x1b[2Kb", "a\rb"):
            with self.assertRaises(review_log.LogDenied) as caught:
                review_log.validate_entry(report(note=payload))
            self.assertEqual(caught.exception.reason,
                             "note_contains_control_characters", repr(payload))

    def test_a_note_cannot_forge_a_second_entry(self):
        """One `review log` call used to produce a file showing a reader an
        independent, attested `security` pass by a seat that does not exist --
        markdown has no escaping and the prose region had no integrity check
        at all."""
        forged = ("real note.\n\n---\n\n### 2026-09-06 · `o/r` PR #1 · "
                  "`security`\n\n| field | value |\n|---|---|\n"
                  "| verdict | **pass** |\n")
        rendered = review_log.render_entry(
            review_log.validate_entry(report(note=forged)))
        headings = [ln for ln in rendered.splitlines()
                    if ln.startswith("### ")]
        self.assertEqual(len(headings), 1, headings)

    def test_a_note_survives_the_round_trip_into_the_rendered_file(self):
        entry = review_log.validate_entry(report(
            note="head not recoverable; findings list not recoverable"))
        self.assertEqual(
            review_log.parse_log(review_log.render_entry(entry))
            ["entries"][0]["note"], entry["note"])

    def test_an_unknown_key_is_refused_rather_than_silently_dropped(self):
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.validate_entry(report(verdit="pass"))
        self.assertEqual(caught.exception.reason, "report_key_unknown")


class RenderedProseTest(unittest.TestCase):
    """The half a person reads was asserted by nothing.

    Five mutants that gutted the table -- dropping both seat rows, dropping
    the note, dropping every disposition line, replacing `independent` and
    `slot` with constants -- all passed the previous suite, because every file
    assertion in it was satisfied by the JSON fence alone.
    """

    def rendered(self, **overrides):
        return review_log.render_entry(
            review_log.validate_entry(report(**overrides)))

    def test_the_fence_is_always_emitted(self):
        """A renderer that omits the fence leaves an entry that reads fine to
        a person and silently stops being counted -- verbatim the failure this
        module exists to prevent."""
        for verdict, findings in (("fail", [dict(BLOCKER)]),
                                  ("fail", [{"severity": "FOLLOW_UP",
                                             "summary": "rename"}]),
                                  ("pass", [])):
            text = self.rendered(verdict=verdict, findings=findings)
            self.assertEqual(len(review_log.BLOCK_FENCE.findall(text)), 1,
                             findings)

    def test_both_seats_appear_in_the_table(self):
        text = self.rendered()
        self.assertIn("| reviewed seat | `crew_alpha/eng_codex` |", text)
        self.assertIn("| reviewing seat | `crew_beta/beta_reviewer_claude` |",
                      text)

    def test_independence_and_slot_are_rendered_not_constant(self):
        held = self.rendered(independent="no", slot_state="held",
                             slot_id="slot-9", slot_absence_reason=None,
                             reviewer_seat="crew_alpha/eng_codex")
        absent = self.rendered()
        self.assertIn("| independent | **NO** |", held)
        self.assertIn("| slot | held (`slot-9`) |", held)
        self.assertIn("| independent | yes |", absent)
        self.assertIn("**absent** — reviewer dispatched outside", absent)

    def test_each_finding_renders_its_severity_and_disposition(self):
        text = self.rendered(findings=[
            dict(BLOCKER, disposition="upheld",
                 disposition_note="checked against the exact tree"),
            {"severity": "FOLLOW_UP", "summary": "rename this"}])
        self.assertIn("- **MERGE_BLOCKER** — `gate/permit.go:228-252` —", text)
        self.assertIn("  - disposition: **upheld** — checked against", text)
        self.assertIn("- **FOLLOW_UP** — rename this", text)
        self.assertIn("  - disposition: **open**", text)

    def test_the_note_is_rendered(self):
        self.assertIn("> a recorded caveat",
                      self.rendered(note="a recorded caveat"))

    def test_the_counts_line_reports_the_real_counts(self):
        text = self.rendered(findings=[
            dict(BLOCKER),
            {"severity": "PRE_DEPLOYMENT", "summary": "cutover evidence"},
            {"severity": "FOLLOW_UP", "summary": "rename"}])
        self.assertIn("Findings — 1 MERGE_BLOCKER, 1 PRE_DEPLOYMENT, "
                      "1 FOLLOW_UP:", text)


class EscapedDefectTest(unittest.TestCase):
    """The number the whole instrument exists to produce.

    Every ordering test here carries a REAL pass and asserts both directions.
    The previous version's ordering test had no pass at all, so the branch
    never ran and three separate ordering mutants survived.
    """

    def test_a_blocker_before_the_pass_has_not_escaped_but_after_it_has(self):
        before = escapes(report(recorded_at="2026-09-06T09:00:00Z"),
                         passing(recorded_at="2026-09-06T12:00:00Z"))
        after = escapes(passing(recorded_at="2026-09-06T09:00:00Z"),
                        report(recorded_at="2026-09-06T12:00:00Z"))
        self.assertEqual(before["escaped"], 0, "before a pass is not an escape")
        self.assertEqual(after["escaped"], 1, "after a pass is an escape")
        self.assertEqual(after["defects"][0]["reason"], "after_pass")
        self.assertEqual(after["defects"][0]["location"],
                         "gate/permit.go:228-252")

    def test_file_order_breaks_a_tie_between_two_entries_in_one_log(self):
        """Whole-second timestamps made ordinary sequential calls collide.
        Within one log the append order is known and is used; two entries with
        no order between them still do not score."""
        after = escapes(passing(recorded_at="2026-09-06T12:00:00Z"),
                        report(recorded_at="2026-09-06T12:00:00Z"))
        before = escapes(report(recorded_at="2026-09-06T12:00:00Z"),
                         passing(recorded_at="2026-09-06T12:00:00Z"))
        self.assertEqual(after["escaped"], 1)
        self.assertEqual(before["escaped"], 0)

    def test_a_pass_does_not_escape_its_own_pass(self):
        """The one place `>` and `>=` differ once ordering is total: the
        entry that established the pass is the same entry carrying the
        finding. It cannot have escaped a gate it *is*."""
        result = escapes(report(verdict="pass",
                                recorded_at="2026-09-06T09:00:00Z"))
        self.assertEqual(result["prs_passed"], 1)
        self.assertEqual(result["escaped"], 0)
        self.assertEqual(result["contradictions"], ["pass_with_merge_blocker"])

    def test_the_earliest_pass_is_the_one_measured_against(self):
        """Keeping the latest pass instead of the earliest silently stops
        counting escapes on any PR reviewed more than once -- and `loops` is a
        first-class field, so re-review is the normal case."""
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T12:00:00Z"),
            passing(recorded_at="2026-09-06T18:00:00Z", head_sha=OTHER_HEAD))
        self.assertEqual(result["escaped"], 1)
        self.assertEqual(result["prs_passed"], 1)

    def test_a_pass_on_a_different_pr_does_not_make_this_one_escape(self):
        result = escapes(
            passing(pr=4212, recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T18:00:00Z"))
        self.assertEqual(result["escaped"], 0)
        self.assertEqual(result["prs_reviewed"], 2)

    def test_prs_reviewed_counts_prs_not_entries(self):
        result = escapes(passing(recorded_at="2026-09-06T09:00:00Z"),
                         report(recorded_at="2026-09-06T18:00:00Z"))
        self.assertEqual(result["entries"], 2)
        self.assertEqual(result["prs_reviewed"], 1)

    def test_a_declared_post_merge_finding_escapes_with_no_pass_recorded(self):
        result = escapes(report(findings=[{
            "severity": "PRE_DEPLOYMENT", "summary": "SCAN ceiling",
            "found_after": "merge"}]))
        self.assertEqual(result["escaped"], 1)
        self.assertEqual(result["defects"][0]["reason"], "declared:merge")

    def test_a_follow_up_after_a_pass_is_not_an_escaped_defect(self):
        result = escapes(passing(recorded_at="2026-09-06T09:00:00Z"),
                         report(recorded_at="2026-09-06T18:00:00Z", findings=[
                             {"severity": "FOLLOW_UP", "summary": "rename"}]))
        self.assertEqual(result["escaped"], 0)

    def test_one_defect_reported_by_two_domains_counts_once(self):
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T18:00:00Z"),
            report(domain="security", recorded_at="2026-09-06T19:00:00Z"))
        self.assertEqual(result["escaped"], 1)
        self.assertEqual(sorted(result["defects"][0]["domains"]),
                         ["cross_crew_review", "security"])

    def test_two_defects_at_different_locations_count_twice(self):
        """Reusing `review consolidate`'s fingerprint collapsed them and
        dropped the second location from the report entirely."""
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T18:00:00Z", findings=[
                dict(BLOCKER, location="a.py:1"),
                dict(BLOCKER, location="b.py:9")]))
        self.assertEqual(result["escaped"], 2)
        self.assertEqual(sorted(d["location"] for d in result["defects"]),
                         ["a.py:1", "b.py:9"])

    def test_the_same_defect_on_two_heads_counts_twice(self):
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T12:00:00Z"),
            report(recorded_at="2026-09-06T18:00:00Z", head_sha=OTHER_HEAD))
        self.assertEqual(result["escaped"], 2)

    def test_the_most_severe_reading_of_one_defect_wins(self):
        """`consolidate_findings` upgrades to the highest severity; keeping
        the first-seen reading contradicted it on identical data."""
        finding = {"criterion": "AC-1", "summary": "same defect",
                   "location": "x.py:1"}
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T12:00:00Z", findings=[
                dict(finding, severity="PRE_DEPLOYMENT")]),
            report(domain="security", recorded_at="2026-09-06T18:00:00Z",
                   findings=[dict(finding, severity="MERGE_BLOCKER")]))
        self.assertEqual(result["escaped"], 1)
        self.assertEqual(result["defects"][0]["severity"], "MERGE_BLOCKER")
        self.assertEqual(result["escaped_by_severity"],
                         {"MERGE_BLOCKER": 1, "PRE_DEPLOYMENT": 0})

    def test_an_overturn_recorded_later_retracts_the_escape(self):
        """Entries are immutable, so an overturn discovered after the review
        was recorded can only arrive as a later entry naming the same finding.
        The previous version ignored it, which made the only reachable case of
        `overturned` unreachable."""
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T12:00:00Z"),
            report(domain="security", recorded_at="2026-09-06T18:00:00Z",
                   findings=[dict(
                       BLOCKER, disposition="overturned",
                       disposition_note="the reviewing seat's sandbox could "
                                        "not read outside its own worktree")]))
        self.assertEqual(result["escaped"], 0)
        self.assertEqual(result["excluded_overturned"], 1)

    def test_a_non_blocking_overturn_cannot_cancel_a_blocker(self):
        """Severity is out of the escape identity so one defect read two ways
        counts once — the cost is that a re-report as FOLLOW_UP shares the
        mark, and marked `overturned` it cancelled a MERGE_BLOCKER. Only a
        blocking finding gets a say in a blocking finding's disposition."""
        finding = {"criterion": "AC-1", "summary": "same text",
                   "location": "x.py:1"}
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T12:00:00Z", findings=[
                dict(finding, severity="MERGE_BLOCKER")]),
            report(domain="qa", recorded_at="2026-09-06T18:00:00Z", findings=[
                dict(finding, severity="FOLLOW_UP", disposition="overturned",
                     disposition_note="not actually a problem")]))
        self.assertEqual(result["escaped"], 1, "a FOLLOW_UP cancelled a blocker")
        self.assertEqual(result["excluded_overturned"], 0)

    def test_an_overturn_at_the_fixed_head_clears_the_earlier_escape(self):
        """An overturn is normally recorded at a LATER head than the finding
        it retracts. Keyed with the head, an honest overturn both failed to
        clear the escape and incremented the exclusion count — one finding
        counted as an escape and a suppressed escape at once."""
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T12:00:00Z"),
            report(recorded_at="2026-09-06T18:00:00Z", head_sha=OTHER_HEAD,
                   findings=[dict(BLOCKER, disposition="overturned",
                                  disposition_note="sandbox could not read "
                                                   "outside its worktree")]))
        self.assertEqual(result["escaped"], 0)
        # Two heads is two escapes by design, and the one overturn suppresses
        # both. Before, it suppressed neither and still counted one.
        self.assertEqual(result["excluded_overturned"], 2)

    def test_an_upheld_finding_still_counts_as_an_escape(self):
        result = escapes(
            passing(recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T18:00:00Z", findings=[dict(
                BLOCKER, disposition="upheld",
                disposition_note="checked against the exact tree")]))
        self.assertEqual(result["escaped"], 1)
        self.assertEqual(result["defects"][0]["disposition"], "upheld")

    def test_an_escape_from_a_seatless_entry_is_counted_not_a_crash(self):
        result = escapes(report(
            author_seat=None, reviewer_seat=None, independent="unknown",
            findings=[{"severity": "MERGE_BLOCKER", "summary": "false pass",
                       "found_after": "review_pass"}]))
        self.assertEqual(result["escaped"], 1)
        self.assertIsNone(result["defects"][0]["reviewer_seat"])

    def test_an_unknown_verdict_never_establishes_a_pass(self):
        result = escapes(
            report(provenance="reconstructed", verdict="unknown",
                   findings_recovered=False, findings=[],
                   recorded_at="2026-09-06T09:00:00Z"),
            report(recorded_at="2026-09-06T18:00:00Z"))
        self.assertEqual(result["prs_passed"], 0)
        self.assertEqual(result["escaped"], 0)
        self.assertEqual(result["entries_with_findings_not_recovered"], 1)

    def test_the_report_says_how_strong_its_own_evidence_is(self):
        result = escapes(
            report(provenance="reconstructed", head_sha="unrecoverable"),
            passing(recorded_at="2026-09-06T11:00:00Z", author_seat=None,
                    reviewer_seat=None, independent="unknown"))
        self.assertEqual(result["entries_by_provenance"],
                         {"attested": 0, "local": 1, "reconstructed": 1})
        self.assertEqual(result["entries_by_independence"],
                         {"yes": 1, "no": 0, "unknown": 1})

    def test_an_attested_entry_with_no_receipt_behind_it_is_named(self):
        """`entry_id` is a digest, not a signature -- anyone who can write the
        file can compute one. The control plane is the only corroboration."""
        forged = review_log.validate_entry(report(
            provenance="attested", comment_url=COMMENT_URL, marker="m"))
        entries = review_log.parse_log(
            review_log.render_entry(forged))["entries"]
        self.assertEqual(
            review_log.escaped_defects(entries, attested_receipts=set())
            ["unverified_attested_entries"], 1)
        self.assertEqual(
            review_log.escaped_defects(
                entries, attested_receipts={("t1", HEAD, "cross_crew_review")})
            ["unverified_attested_entries"], 0)


class AppendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "reviews.md")

    def test_the_log_is_created_with_its_header_in_one_write(self):
        review_log.append_entry(self.path, review_log.validate_entry(report()))
        text = Path(self.path).read_text(encoding="utf-8")
        self.assertTrue(text.startswith(review_log.HEADER))
        self.assertEqual(len(review_log.parse_log(text)["entries"]), 1)

    def test_a_concurrent_creator_cannot_overwrite_an_appended_entry(self):
        """The header used to be written on a separate non-append fd and
        buffered to offset 0, so a concurrent appender's entry could be
        stomped by the header landing on top of it -- both writers reporting
        success, one review gone. The window is widened here so the test is
        deterministic rather than a matter of timing."""
        script = os.path.join(self.tmp.name, "w.py")
        Path(script).write_text(
            "import os, sys, time\n"
            "sys.path.insert(0, %r)\n" % os.path.join(REPO, "bin") +
            "import nightshift_review_log as L\n"
            "n = int(sys.argv[1])\n"
            "if n: time.sleep(0.15)\n"
            "L.append_entry(sys.argv[2], L.validate_entry({\n"
            "  'repo': 'o/r', 'pr': 1, 'task_id': 't', 'head_sha': 'a'*40,\n"
            "  'domain': 'qa', 'verdict': 'fail', 'independent': 'unknown',\n"
            "  'slot_state': 'unknown',\n"
            "  'recorded_at': '2026-09-0%dT10:00:00Z' % (n + 1),\n"
            "  'findings': [{'severity': 'MERGE_BLOCKER',\n"
            "                'summary': 'blocker %d' % n}]}))\n",
            encoding="utf-8")
        procs = [subprocess.Popen([sys.executable, script, str(n), self.path])
                 for n in (0, 1)]
        for proc in procs:
            self.assertEqual(proc.wait(), 0)
        text = Path(self.path).read_text(encoding="utf-8")
        self.assertEqual(len(review_log.parse_log(text)["entries"]), 2,
                         "a concurrent writer's entry was overwritten")

    def test_a_short_write_is_an_error_not_a_silent_truncation(self):
        """`ENOSPC` returns a partial count rather than raising, so ignoring
        the return value leaves half an entry that poisons every later read
        while the caller is told the review was recorded."""
        real_write = review_log.os.write

        def short(fd, data):
            return real_write(fd, data[:len(data) // 2])

        review_log.os.write = short
        self.addCleanup(setattr, review_log.os, "write", real_write)
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.append_entry(self.path,
                                    review_log.validate_entry(report()))
        self.assertEqual(caught.exception.reason, "reviews_write_truncated")

    def test_a_symlinked_log_is_refused_and_its_target_untouched(self):
        """Audit C3, replacing the test that pinned write-through: the target
        repository can track `.omni/reviews.md` as a symlink to `~/.bashrc`,
        and following it appended review text there. Shared storage is
        reached by passing its real path, never by a link."""
        real = os.path.join(self.tmp.name, "bashrc")
        Path(real).write_text("export PS1='$ '\n", encoding="utf-8")
        link = os.path.join(self.tmp.name, "reviews.md")
        os.symlink(real, link)
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.append_entry(link, review_log.validate_entry(report()))
        self.assertEqual(caught.exception.reason, "reviews_is_a_symlink")
        self.assertEqual(Path(real).read_text(encoding="utf-8"),
                         "export PS1='$ '\n")

    def test_a_dangling_symlink_is_refused_not_created(self):
        target = os.path.join(self.tmp.name, "authorized_keys")
        link = os.path.join(self.tmp.name, "reviews.md")
        os.symlink(target, link)
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.append_entry(link, review_log.validate_entry(report()))
        self.assertEqual(caught.exception.reason, "reviews_is_a_symlink")
        self.assertFalse(os.path.exists(target))

    def test_a_symlinked_omni_directory_is_refused(self):
        elsewhere = os.path.join(self.tmp.name, "home")
        os.mkdir(elsewhere)
        omni = os.path.join(self.tmp.name, ".omni")
        os.symlink(elsewhere, omni)
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.append_entry(os.path.join(omni, "reviews.md"),
                                    review_log.validate_entry(report()))
        self.assertEqual(caught.exception.reason, "reviews_dir_untrusted")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_a_symlink_to_a_fifo_is_still_refused(self):
        """The fifo fix stays closed through a link: refused, never opened.
        The reason is the link itself now, which is checked first."""
        fifo = os.path.join(self.tmp.name, "f")
        link = os.path.join(self.tmp.name, "reviews.md")
        os.mkfifo(fifo)
        os.symlink(fifo, link)
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.append_entry(link, review_log.validate_entry(report()))
        self.assertEqual(caught.exception.reason, "reviews_is_a_symlink")

    def test_a_fifo_is_refused_and_does_not_block(self):
        """The type check ran on the descriptor, which was too late: opening a
        fifo write-only blocks until a reader appears, so the check never ran
        — and the caller was holding the control-plane lock while it hung."""
        fifo = os.path.join(self.tmp.name, "fifo.md")
        os.mkfifo(fifo)
        script = os.path.join(self.tmp.name, "f.py")
        Path(script).write_text(
            "import sys\n"
            "sys.path.insert(0, %r)\n" % os.path.join(REPO, "bin") +
            "import nightshift_review_log as L\n"
            "try:\n"
            "    L.append_entry(sys.argv[1], L.validate_entry({\n"
            "      'repo':'o/r','pr':1,'task_id':'t','head_sha':'a'*40,\n"
            "      'domain':'qa','verdict':'fail','independent':'unknown',\n"
            "      'slot_state':'unknown',\n"
            "      'recorded_at':'2026-09-06T10:00:00Z','findings':[]}))\n"
            "    print('ACCEPTED')\n"
            "except L.LogDenied as e: print(e.reason)\n", encoding="utf-8")
        proc = subprocess.run([sys.executable, script, fifo],
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.stdout.strip(), "reviews_not_a_regular_file")

    def test_the_log_must_be_a_regular_file(self):
        """`/dev/null` accepted every write, returned success and recorded
        nothing, while the caller went on to mint a receipt pointing at an
        entry that exists nowhere."""
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.append_entry("/dev/null",
                                    review_log.validate_entry(report()))
        self.assertEqual(caught.exception.reason,
                         "reviews_not_a_regular_file")

    def test_an_unwritable_target_is_refused_by_name(self):
        blocked = os.path.join(self.tmp.name, "blocked")
        os.makedirs(blocked)
        with self.assertRaises(review_log.LogDenied) as caught:
            review_log.append_entry(blocked,
                                    review_log.validate_entry(report()))
        self.assertEqual(caught.exception.reason,
                         "reviews_not_a_regular_file")


class LegacyLogTest(unittest.TestCase):
    """A file full of pre-ADR-0090 rows is not a file nobody wrote to.

    The first version of this reader returned `no_entries` for the
    repository's own 634-line log -- which holds 23 recorded reviews -- and
    the retro skill told the reader to conclude that nobody had recorded them.
    That is this ticket's defect, committed by its own instrument.
    """

    def test_legacy_rows_are_counted_even_though_they_do_not_parse(self):
        legacy = (review_log.HEADER
                  + "\n| Date | PR | Author crew |\n|---|---|---|\n"
                  + "| 2026-08-23 | #15 | crew_beta |\n"
                  + "| 2026-08-29 | #315 | crew_alpha |\n"
                  + "\n## 2026-08-30 crew_beta on #349\n\nprose verdict.\n")
        result = review_log.parse_log(legacy)
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["legacy_rows"], 3)

    def test_a_modern_entry_is_never_counted_as_a_legacy_row(self):
        """An ADR-0090 heading is `### YYYY-MM-DD · `, which matches the
        legacy prose-heading shape unless excluded — inflating the very count
        that is meant to tell a reader the file predates the format."""
        text = "".join(review_log.render_entry(review_log.validate_entry(r))
                       for r in (report(), passing(pr=4212)))
        result = review_log.parse_log(text)
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["legacy_rows"], 0)
        self.assertEqual(result["headings"] - result["fences"], 0)

    def test_legacy_rows_are_recognised_through_ordinary_spacing(self):
        """A row a person would obviously read as a legacy entry must not be
        missed for want of a space — the miss turns `legacy_entries_only`
        back into `no_entries`, which is the wrong diagnosis."""
        for row in ("|  2026-08-23  | #15 |", "|2026-08-23|#15|",
                    "   | 2026-08-23 | #15 |", "#### 2026-08-30 crew_beta",
                    "  ## 2026-08-30 crew_beta"):
            result = review_log.parse_log(review_log.HEADER + "\n" + row + "\n")
            self.assertEqual(result["legacy_rows"], 1, row)

    def test_the_repositorys_own_log_is_not_reported_as_empty(self):
        path = os.path.join(REPO, ".omni/reviews.md")
        if not os.path.exists(path):
            self.skipTest("no control plane in this tree")
        result = review_log.read_log(path)
        # Deliberately NOT asserting zero fenced entries. This log is tracked,
        # and this PR's own skill now tells every seat to append to it -- so
        # asserting it stays empty would turn green to red for an unrelated
        # author the first time the feature is used as documented.
        self.assertEqual(result["unreadable"], [],
                         "the repository's own log must stay readable")
        self.assertGreater(
            result["legacy_rows"], 0,
            "the repository's own review log holds recorded reviews; a reader "
            "that reports zero of them is the defect #717 exists to fix")


class ReviewLogCliTest(unittest.TestCase):
    """Drives the real binary."""

    def setUp(self):
        self.ctl = Ctl()
        self.reviews = os.path.join(self.ctl.omni, "reviews.md")
        code, body = self.ctl.run(
            "task", "upsert", "--id", "t1", "--crew", "crew_alpha",
            "--agent", "eng_codex", "--title", "redis", "--state", "pending",
            "--size", "S", "--pr-url", PR_URL)
        self.assertEqual(code, 0, body)

    def log(self, payload, *extra):
        return self.ctl.run("review", "log", "--report-json", "-", *extra,
                            stdin=json.dumps(payload))

    def rows(self, table):
        with sqlite3.connect(self.ctl.db) as cx:
            cx.row_factory = sqlite3.Row
            return [dict(r) for r in cx.execute(f"SELECT * FROM {table}")]

    def test_review_log_writes_the_file_and_mints_a_receipt(self):
        code, body = self.log(report())
        self.assertEqual(code, 0, body)
        self.assertTrue(body["receipt_recorded"], body)

        text = Path(self.reviews).read_text(encoding="utf-8")
        self.assertIn("#4211", text)
        self.assertIn("crew_alpha/eng_codex", text)
        self.assertIn("crew_beta/beta_reviewer_claude", text)
        self.assertIn("MERGE_BLOCKER", text)
        self.assertIn("gate/permit.go:228-252", text)

        receipt_rows = self.rows("review_receipts")
        self.assertEqual(len(receipt_rows), 1, receipt_rows)
        self.assertEqual(receipt_rows[0]["source"], "local")
        self.assertEqual(receipt_rows[0]["comment_url"],
                         f"omni://reviews.md#{body['entry_id']}")
        self.assertEqual(len(self.rows("review_findings")), 1)

    def test_the_release_gates_missing_domains_reflects_a_logged_review(self):
        code, body = self.ctl.run("review", "consolidate", "--task", "t1",
                                  "--head-sha", HEAD, "--contract", CONTRACT)
        self.assertEqual(code, 0, body)
        self.assertIn("cross_crew_review", body["missing_domains"])
        self.assertEqual(self.log(report())[0], 0)
        code, body = self.ctl.run("review", "consolidate", "--task", "t1",
                                  "--head-sha", HEAD, "--contract", CONTRACT)
        self.assertEqual(code, 0, body)
        self.assertNotIn("cross_crew_review", body["missing_domains"])

    def test_a_review_with_no_task_row_still_lands_in_the_file(self):
        code, body = self.log(report(task_id="never-admitted"))
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"])
        self.assertEqual(body["receipt_skipped_reason"], "task_missing")
        self.assertIn("never-admitted",
                      Path(self.reviews).read_text(encoding="utf-8"))
        self.assertEqual(self.rows("review_receipts"), [])

    def test_review_log_refuses_to_mint_an_attested_entry(self):
        code, body = self.log(report(provenance="attested",
                                     comment_url=COMMENT_URL, marker="x"))
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "attested_requires_receipt_record")
        self.assertFalse(os.path.exists(self.reviews))

    def test_a_malformed_report_writes_nothing_at_all(self):
        code, body = self.log(report(head_sha="short"))
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "head_sha_invalid")
        self.assertFalse(os.path.exists(self.reviews))
        self.assertEqual(self.rows("review_receipts"), [])

    def test_an_out_of_vocabulary_review_is_logged_with_no_receipt(self):
        code, body = self.log(report(domain="rollout_sequence",
                                     domain_outside_vocabulary=True))
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"])
        self.assertEqual(body["receipt_skipped_reason"],
                         "domain_outside_vocabulary")
        self.assertIn("rollout_sequence",
                      Path(self.reviews).read_text(encoding="utf-8"))
        self.assertEqual(self.rows("review_receipts"), [])

    def test_escaped_defects_refuses_an_empty_read(self):
        code, body = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "reviews_missing")
        self.assertEqual(self.log(report())[0], 0)
        code, body = self.ctl.run("review", "escaped-defects", "--pr", "42")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "no_entries_in_window")

    def test_a_mangled_file_is_not_reported_as_an_empty_one(self):
        """The refusal payload dropped the one number that separates "the
        fences are gone" from "nobody wrote anything" — omitted from exactly
        the branch that reports emptiness."""
        self.assertEqual(self.log(report())[0], 0)
        text = Path(self.reviews).read_text(encoding="utf-8")
        stripped = re.sub(r"```nightshift-review-log\n.*?\n```", "", text,
                          flags=re.S)
        Path(self.reviews).write_text(stripped, encoding="utf-8")
        code, body = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "no_entries")
        self.assertEqual(body["entries_without_fence"], 1,
                         "a heading with no fence must be named in the refusal")

    def test_the_windowed_number_is_the_headline_not_a_footnote(self):
        """`--since` filtered only a sub-object the retro skill never
        mentions, while the skill prints the `--since` recipe and then says to
        quote `escaped` — so the published per-wave metric was the cumulative
        all-time count, a number that can only grow."""
        self.assertEqual(self.log(passing(
            recorded_at="2026-01-01T09:00:00Z"))[0], 0)
        self.assertEqual(self.log(report(
            recorded_at="2026-01-02T09:00:00Z"))[0], 0)
        self.assertEqual(self.log(report(
            domain="qa", recorded_at="2026-06-01T09:00:00Z",
            findings=[dict(BLOCKER, location="other.go:9",
                           summary="a different defect")]))[0], 0)
        code, body = self.ctl.run("review", "escaped-defects",
                                  "--since", "2026-05-01T00:00:00Z")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["escaped"], 1, "the headline is the wave's")
        self.assertEqual(body["whole_log"]["escaped"], 2)

    def test_an_unreadable_log_is_not_an_unwritten_one(self):
        """The retro branches on this exact string, and `no_entries` tells it
        reviews that happened were not recorded — about a file holding
        nothing but genuinely recorded reviews it could not parse."""
        self.assertEqual(self.log(report())[0], 0)
        text = Path(self.reviews).read_text(encoding="utf-8")
        Path(self.reviews).write_text(text.replace('"schema":1', '"schema":9'),
                                      encoding="utf-8")
        code, body = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "entries_unreadable")
        self.assertEqual(body["unreadable_entries"][0]["reason"],
                         "schema_unsupported")

    def test_a_reconstruction_with_no_head_never_mints_a_receipt(self):
        """ADR-0040's reuse branch selects `head_sha != ?` and would otherwise
        report `satisfied_by: reused, head_sha: unrecoverable`."""
        code, body = self.log(report(provenance="reconstructed",
                                     head_sha="unrecoverable"))
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"])
        self.assertEqual(body["receipt_skipped_reason"],
                         "head_sha_unrecoverable")
        self.assertEqual(self.rows("review_receipts"), [])

    def test_a_non_utf8_log_is_refused_not_a_traceback(self):
        Path(self.reviews).write_bytes(b"# log\n\n\xff\xfe garbage\n")
        code, body = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "reviews_not_utf8")

    def test_escaped_defects_counts_a_real_escape_through_the_cli(self):
        self.assertEqual(self.log(passing(
            recorded_at="2026-09-06T09:00:00Z"))[0], 0)
        self.assertEqual(self.log(report(
            recorded_at="2026-09-06T18:00:00Z"))[0], 0)
        code, body = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["escaped"], 1)
        self.assertEqual(body["entries"], 2)
        self.assertEqual(body["prs_passed"], 1)

    def test_a_window_does_not_hide_the_escape_it_contains(self):
        """The retro's own documented per-wave invocation. Filtering entries
        before the computation dropped the pass that predated the window, so
        the blocker inside it was no longer "after a pass" and reported 0."""
        self.assertEqual(self.log(passing(
            recorded_at="2026-09-01T09:00:00Z"))[0], 0)
        self.assertEqual(self.log(report(
            recorded_at="2026-09-08T18:00:00Z"))[0], 0)
        code, full = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 0, full)
        self.assertEqual(full["escaped"], 1)
        code, body = self.ctl.run("review", "escaped-defects",
                                  "--since", "2026-09-07T00:00:00Z")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["window"]["escaped"], 1,
                         "the window hid the escape it contains")
        self.assertEqual(body["window"]["entries"], 1)

    def test_a_defect_reported_again_inside_the_window_is_in_the_window(self):
        """The window filtered on the FIRST sighting, so a defect re-reported
        during the wave was excluded because it had first been seen before
        it."""
        self.assertEqual(self.log(passing(
            recorded_at="2026-09-01T09:00:00Z"))[0], 0)
        self.assertEqual(self.log(report(
            recorded_at="2026-09-02T09:00:00Z"))[0], 0)
        self.assertEqual(self.log(report(
            domain="security", recorded_at="2026-09-08T09:00:00Z"))[0], 0)
        code, body = self.ctl.run("review", "escaped-defects",
                                  "--since", "2026-09-07T00:00:00Z")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["window"]["escaped"], 1)

    def test_a_pr_number_alone_does_not_span_repositories(self):
        self.assertEqual(self.log(passing(
            recorded_at="2026-09-01T09:00:00Z"))[0], 0)
        self.assertEqual(self.log(report(
            recorded_at="2026-09-02T09:00:00Z"))[0], 0)
        self.ctl.run("task", "upsert", "--id", "t2", "--crew", "crew_alpha",
                     "--agent", "eng_codex", "--title", "x", "--state",
                     "pending", "--size", "S", "--pr-url",
                     "https://github.com/other-org/other-service/pull/4211")
        self.assertEqual(self.log(passing(
            task_id="t2", repo="other-org/other-service",
            recorded_at="2026-09-01T10:00:00Z"))[0], 0)
        self.assertEqual(self.log(report(
            task_id="t2", repo="other-org/other-service", domain="security",
            recorded_at="2026-09-02T10:00:00Z"))[0], 0)
        code, both = self.ctl.run("review", "escaped-defects", "--pr", "4211")
        self.assertEqual(code, 0, both)
        code, one = self.ctl.run("review", "escaped-defects", "--pr", "4211",
                                 "--repo", "example-org/example-service")
        self.assertEqual(code, 0, one)
        self.assertLess(one["window"]["escaped"], both["window"]["escaped"],
                        "--repo must narrow a PR number to one repository")

    def test_an_entry_deleted_from_the_file_is_visible_from_the_plane(self):
        """A whole entry removed leaves nothing in the file to be missing.
        The plane is the second record."""
        self.assertEqual(self.log(report())[0], 0)
        Path(self.reviews).write_text(review_log.HEADER, encoding="utf-8")
        code, body = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 3, body)
        code, body = self.ctl.run("review", "escaped-defects",
                                  "--reviews-path", self.reviews)
        self.assertEqual(code, 3, body)
        self.assertEqual(self.log(passing(domain="qa"))[0], 0)
        code, body = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 0, body)
        self.assertTrue(body["receipts_without_entries"],
                        "a receipt whose entry is gone must be named")

    def test_a_second_log_cannot_erase_the_first_ones_findings(self):
        """One command used to destroy a review. A reviewer logs a
        MERGE_BLOCKER; the author logs a clean pass for the same task, head
        and domain; the upsert flipped the verdict and DELETEd the finding —
        the file still showed the blocker, the plane said the domain was
        clean. The earlier guard tested `source != provenance`, which lets
        this through because both rows are `local`."""
        self.assertEqual(self.log(report())[0], 0)
        self.assertEqual(self.rows("review_receipts")[0]["verdict"], "fail")
        self.assertEqual(len(self.rows("review_findings")), 1)

        code, body = self.log(passing())
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"])
        self.assertEqual(body["receipt_skipped_reason"],
                         "existing_receipt_from_local")
        self.assertEqual(self.rows("review_receipts")[0]["verdict"], "fail")
        self.assertEqual(len(self.rows("review_findings")), 1,
                         "a recorded finding was deleted")

    def test_a_report_may_not_declare_another_repositorys_pr(self):
        """`pr_url` was built from the report's self-declared repo and PR and
        written into a NOT NULL column whose only job is to locate the PR."""
        code, body = self.log(report(repo="attacker/elsewhere", pr=999))
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"])
        self.assertEqual(body["receipt_skipped_reason"],
                         "pr_url_disagrees_with_task")
        self.assertEqual(self.rows("review_receipts"), [])
        self.assertIn("attacker/elsewhere",
                      Path(self.reviews).read_text(encoding="utf-8"))

    def test_a_resolved_finding_does_not_block_the_head_forever(self):
        """`review_findings` is what eligibility gates on and has no
        disposition column, so honestly logging an already-fixed blocker
        blocked that head permanently — punishing the honest record and
        rewarding a re-log with no findings at all."""
        code, body = self.log(report(verdict="pass", findings=[
            dict(BLOCKER, disposition="fixed",
                 disposition_note="closed in a later commit")]))
        self.assertEqual(code, 0, body)
        self.assertTrue(body["receipt_recorded"])
        self.assertEqual(self.rows("review_findings"), [],
                         "a resolved finding must not gate the head")
        self.assertIn("MERGE_BLOCKER",
                      Path(self.reviews).read_text(encoding="utf-8"))

    def test_an_unvalidated_receipt_claims_no_ruleset(self):
        """A digest means "validated against this ruleset". ADR-0040 treats
        NULL as drift and fails closed; stamping today's digest onto a review
        never validated against any ruleset turned that into a positive
        no-drift assertion."""
        self.assertEqual(self.log(report())[0], 0)
        self.assertIsNone(self.rows("review_receipts")[0]["rules_digest"])

    def test_a_logged_review_never_overwrites_an_attested_receipt(self):
        """The upsert is shared with the ADR-0039 path, so an unattested
        `review log` used to overwrite an attested receipt's verdict and
        delete its findings."""
        with sqlite3.connect(self.ctl.db) as cx:
            cx.execute(
                "INSERT INTO review_receipts(task_id,head_sha,domain,verdict,"
                "pr_url,comment_url,marker,report_digest,comment_created_at,"
                "validated_at,created_at,source) "
                "VALUES('t1',?,'cross_crew_review','fail',?,?,'mk','d',"
                "'2026-09-06T00:00:00Z',1,1,'attested')",
                (HEAD, PR_URL, COMMENT_URL))
        code, body = self.log(passing())
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"])
        self.assertEqual(body["receipt_skipped_reason"],
                         "existing_receipt_from_attested")
        row = self.rows("review_receipts")[0]
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["comment_url"], COMMENT_URL)
        self.assertIn("#4211", Path(self.reviews).read_text(encoding="utf-8"))

    def test_a_receipt_written_before_the_migration_is_also_protected(self):
        """`source` was added with no backfill, so every receipt older than
        migration 13 is NULL. Testing for `== "attested"` failed OPEN on
        exactly those — the oldest and best-evidenced rows in the table."""
        with sqlite3.connect(self.ctl.db) as cx:
            cx.execute(
                "INSERT INTO review_receipts(task_id,head_sha,domain,verdict,"
                "pr_url,comment_url,marker,report_digest,comment_created_at,"
                "validated_at,created_at) "
                "VALUES('t1',?,'cross_crew_review','fail',?,?,'mk','d',"
                "'2026-09-06T00:00:00Z',1,1)",
                (HEAD, PR_URL, COMMENT_URL))
        code, body = self.log(report(provenance="reconstructed",
                                     findings_recovered=False, findings=[]))
        self.assertEqual(code, 0, body)
        self.assertFalse(body["receipt_recorded"])
        self.assertEqual(body["receipt_skipped_reason"],
                         "existing_receipt_from_before_migration_13")
        row = self.rows("review_receipts")[0]
        self.assertEqual(row["verdict"], "fail")
        self.assertEqual(row["marker"], "mk")

    def test_a_review_records_even_with_no_control_plane_at_all(self):
        """The verb whose thesis is "the finding outranks the accounting" was
        exiting before it ran when the plane could not be resolved — for
        exactly the population ADR-0090 exists to serve."""
        alone = os.path.join(self.ctl.dir, "no-plane")
        os.makedirs(alone)
        path = os.path.join(alone, "reviews.md")
        proc = subprocess.run(
            [CTL, "--db", os.path.join(alone, "absent.db"), "review", "log",
             "--report-json", "-", "--reviews-path", path],
            input=json.dumps(report()), capture_output=True, text=True)
        body = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(body["ok"], body)
        self.assertFalse(body["receipt_recorded"])
        self.assertEqual(body["receipt_skipped_reason"],
                         "control_plane_missing")
        self.assertIn("#4211", Path(path).read_text(encoding="utf-8"))

    def test_the_log_survives_being_written_by_two_seats_at_once(self):
        procs = [
            subprocess.Popen(
                self.ctl.cmdline("review", "log", "--report-json", "-"),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            for _ in range(6)
        ]
        for index, proc in enumerate(procs):
            proc.stdin.write(json.dumps(report(
                domain=receipts.CANONICAL_DOMAINS[index],
                recorded_at=f"2026-09-06T1{index}:00:00Z")))
            proc.stdin.close()
        for proc in procs:
            self.assertEqual(proc.wait(), 0, proc.stderr.read())
        code, body = self.ctl.run("review", "escaped-defects")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["entries"], 6, "a concurrent append was lost")


class DiffOfAPreExistingFile(unittest.TestCase):
    """AC-1 says "shown by diffing the file, not asserted".

    The previous test compared `before` to `after` where `before` was always
    `""`, because `setUp` never created the file -- so the assertion reduced
    to `after != ""` and its failure message described a comparison that never
    happened. This one appends to a log that already has an entry in it.
    """

    def setUp(self):
        self.ctl = Ctl()
        self.reviews = os.path.join(self.ctl.omni, "reviews.md")
        for task, url in (("t1", PR_URL), ("t2", OTHER_PR_URL)):
            self.ctl.run("task", "upsert", "--id", task, "--crew",
                         "crew_alpha", "--agent", "eng_codex", "--title", "x",
                         "--state", "pending", "--size", "S", "--pr-url", url)

    def log(self, payload):
        return self.ctl.run("review", "log", "--report-json", "-",
                            stdin=json.dumps(payload))

    def test_a_second_review_adds_one_entry_and_rewrites_nothing(self):
        self.assertEqual(self.log(report(
            recorded_at="2026-09-06T09:00:00Z"))[0], 0)
        before = Path(self.reviews).read_text(encoding="utf-8")
        self.assertNotEqual(before, "", "sanity: the log already has content")

        code, body = self.log(report(
            task_id="t2", pr=4212, domain="qa",
            recorded_at="2026-09-06T18:00:00Z",
            findings=[{"severity": "PRE_DEPLOYMENT",
                       "summary": "cutover needs live evidence"}]))
        self.assertEqual(code, 0, body)
        after = Path(self.reviews).read_text(encoding="utf-8")

        self.assertTrue(after.startswith(before),
                        "an append must not rewrite what was already there")
        added = after[len(before):]
        self.assertIn("#4212", added)
        self.assertIn("PRE_DEPLOYMENT", added)
        self.assertIn("cutover needs live evidence", added)
        self.assertEqual(len(review_log.BLOCK_FENCE.findall(added)), 1)
        self.assertEqual(len(review_log.parse_log(after)["entries"]), 2)


class ReceiptRecordWritesTheLogTest(unittest.TestCase):
    """An ADR-0039 receipt cannot be persisted without its entry landing in
    `.omni/reviews.md` first."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctl = Ctl()
        self.reviews = os.path.join(self.ctl.omni, "reviews.md")
        self.ctl.run("task", "upsert", "--id", "t1", "--crew", "crew_alpha",
                     "--agent", "eng_codex", "--title", "redis",
                     "--state", "pending", "--size", "S", "--pr-url", PR_URL)
        self.gh_path = os.path.join(self.tmp.name, "fake_gh.py")
        Path(self.gh_path).write_text(FAKE_GH, encoding="utf-8")
        os.chmod(self.gh_path, 0o755)
        self.response_path = os.path.join(self.tmp.name, "response.json")
        for name, value in (("NIGHTSHIFT_CTL_GH", self.gh_path),
                            ("FAKE_GH_RESPONSE_FILE", self.response_path)):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)

    def record(self, *extra, findings=(), verdict="fail"):
        code, body = self.ctl.run("review", "marker-mint", "--task", "t1",
                                  "--head-sha", HEAD, "--domain",
                                  "cross_crew_review")
        self.assertEqual(code, 0, body)
        block = {"domain": "cross_crew_review", "task_id": "t1",
                 "head_sha": HEAD, "verdict": verdict,
                 "marker": body["marker"], "findings": list(findings)}
        Path(self.response_path).write_text(json.dumps({
            "id": 555,
            "body": "preamble\n\n```nightshift-review\n"
                    + json.dumps(block) + "\n```\n",
            "created_at": "2026-09-06T10:26:50Z",
            "updated_at": "2026-09-06T10:26:50Z",
            "html_url": COMMENT_URL,
            "user": {"login": "nightshift-bot"},
        }), encoding="utf-8")
        return self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "cross_crew_review", "--comment-url", COMMENT_URL,
            *extra)

    def entry(self):
        return review_log.parse_log(
            Path(self.reviews).read_text(encoding="utf-8"))["entries"][0]

    def receipt_count(self):
        with sqlite3.connect(self.ctl.db) as cx:
            return cx.execute(
                "SELECT COUNT(*) FROM review_receipts").fetchone()[0]

    def test_receipt_record_writes_the_reviews_log(self):
        self.assertFalse(os.path.exists(self.reviews))
        code, body = self.record(findings=[
            {"severity": "PRE_DEPLOYMENT", "criterion": "AC-3",
             "summary": "cutover needs live evidence"}])
        self.assertEqual(code, 0, body)
        text = Path(self.reviews).read_text(encoding="utf-8")
        self.assertIn("PRE_DEPLOYMENT", text)
        self.assertIn("`attested`", text)
        self.assertIn(COMMENT_URL, text)
        self.assertEqual(self.entry()["provenance"], "attested")

    def test_an_undeclared_seat_is_logged_as_unknown_never_guessed(self):
        code, body = self.record()
        self.assertEqual(code, 0, body)
        entry = self.entry()
        self.assertEqual(entry["independent"], "unknown")
        self.assertNotIn("reviewer_seat", entry)
        self.assertEqual(entry["slot_state"], "unknown")

    def test_declared_seats_reach_the_log(self):
        code, body = self.record(
            "--author-seat", "crew_alpha/eng_codex",
            "--reviewer-seat", "crew_beta/beta_reviewer_claude",
            "--vendor", "Claude", "--independent", "yes", "--loops", "2")
        self.assertEqual(code, 0, body)
        entry = self.entry()
        self.assertEqual(entry["reviewer_seat"],
                         "crew_beta/beta_reviewer_claude")
        self.assertEqual(entry["independent"], "yes")
        self.assertEqual(entry["loops"], 2)

    def test_a_validated_receipt_is_not_lost_to_bad_caller_metadata(self):
        """The receipt already passed ADR-0039's matrix. Discarding it because
        a caller mistyped a seat would throw away validated evidence over
        presentation metadata."""
        code, body = self.record("--author-seat", "not a seat",
                                 "--reviewer-seat", "crew_beta/r")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["entry_degraded_reason"], "seat_invalid")
        self.assertEqual(self.entry()["independent"], "unknown")
        self.assertEqual(self.receipt_count(), 1,
                         "the validated receipt was lost")

    def test_a_finding_containing_a_code_fence_does_not_lose_the_receipt(self):
        code, body = self.record(findings=[
            {"severity": "FOLLOW_UP", "criterion": None,
             "summary": "use ```go fences``` here"}])
        self.assertEqual(code, 0, body)
        self.assertEqual(self.receipt_count(), 1)
        self.assertEqual(len(review_log.parse_log(
            Path(self.reviews).read_text(encoding="utf-8"))["entries"]), 1)

    def test_a_receipt_is_refused_when_the_log_cannot_be_written(self):
        blocked = os.path.join(self.ctl.omni, "blocked")
        os.makedirs(blocked)
        code, body = self.record("--reviews-path", blocked)
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "reviews_not_a_regular_file")
        self.assertEqual(self.receipt_count(), 0,
                         "a receipt was persisted with no log entry")


class SkillsInvokeTheseCommandsTest(unittest.TestCase):
    """Presence is not function, made executable.

    Three separate misses on the external-repository run were artifacts that
    existed, were well made, and were invoked by nothing. A recording command
    that no skill names is the same shape, so grep for the invoker rather than
    trusting that the file exists.
    """

    def read(self, relpath):
        return Path(os.path.join(REPO, relpath)).read_text(encoding="utf-8")

    def test_the_cross_crew_review_skill_names_the_recording_command(self):
        text = self.read("program/skills/cross-crew-review/SKILL.md")
        self.assertIn("nightshift-ctl review log", text)
        self.assertIn(".omni/reviews.md", text)

    def test_the_retro_skill_names_the_escaped_defect_command(self):
        self.assertIn("nightshift-ctl review escaped-defects",
                      self.read("program/skills/retro/SKILL.md"))

    def test_the_skill_lists_exactly_the_frozen_domain_vocabulary(self):
        text = self.read("program/skills/cross-crew-review/SKILL.md")
        sentence = re.search(r"The frozen\s+set is (.+?)\.", text, re.S)
        self.assertIsNotNone(sentence, "the skill no longer lists the set")
        self.assertEqual(re.findall(r"`([a-z_]+)`", sentence.group(1)),
                         list(receipts.CANONICAL_DOMAINS))

    def test_the_log_header_is_identical_in_both_writers(self):
        shell = self.read("bin/nightshift-init.sh")
        # init.sh writes every state file through `new_file` (audit C3).
        opener = "new_file .omni/reviews.md <<'EOF'\n"
        start = shell.index(opener) + len(opener)
        end = shell.index("\nEOF\n", start) + 1
        self.assertEqual(shell[start:end], review_log.HEADER)
        # A line that is exactly `EOF'` is a stray terminator: the heredoc
        # opener is `<<'EOF'`, so the delimiter is `EOF`, and `EOF'` does not
        # close it -- the heredoc runs on and swallows whatever follows.
        # `<<'EOF'` itself contains the substring, so this must be anchored.
        self.assertIsNone(re.search(r"^EOF'\s*$", shell, re.M),
                          "a stray terminator makes the heredoc run on")

    def test_the_header_cannot_be_parsed_as_an_entry(self):
        self.assertIsNone(review_log.BLOCK_FENCE.search(review_log.HEADER))

    def test_both_commands_are_real_verbs_on_the_binary(self):
        """Uses `CTL`, so `$NIGHTSHIFT_TEST_CTL` actually points it at the
        binary under test. Pointed at `REPO` it reported that the verbs were
        real about a binary the run was not testing."""
        for verb in ("log", "escaped-defects"):
            proc = subprocess.run([CTL, "review", verb, "--help"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0,
                             f"`review {verb} --help` failed: {proc.stderr}")


FAKE_GH = '''#!/usr/bin/env python3
import os, sys
if sys.argv[1:] == ["api", "--method", "GET", "user"]:
    print('{"login": "nightshift-bot"}')
    sys.exit(0)
with open(os.environ["FAKE_GH_RESPONSE_FILE"], "r", encoding="utf-8") as h:
    sys.stdout.write(h.read())
sys.exit(0)
'''


if __name__ == "__main__":
    unittest.main()
