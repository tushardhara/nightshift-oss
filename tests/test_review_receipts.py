"""ADR-0039 / issue #231: persisted review receipts and consolidated findings.

Two layers, matching the module split (`bin/nightshift_review_receipts.py` is
read/validate-only; `bin/nightshift-ctl` owns every write):

- Fast unit tests call `validate_receipt`/`consolidate_findings`/
  `parse_required_domains` directly with an injected `fetch_comment_fn`, no
  subprocess, one per fail-closed branch of AC-3 plus the happy path.
- A smaller set of CLI integration tests drives the real binary (`Ctl`,
  matching every other control-plane test) with a tiny fake-`gh` stub, to
  prove the wiring -- schema, marker-mint, receipt-record, consolidate --
  actually persists and reads back through SQLite, and that the DB CHECK
  constraints on domain/verdict/severity are real.
"""

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import textwrap
import threading
import time
import unittest

from ctlutil import REPO, Ctl

sys.path.insert(0, os.path.join(REPO, "bin"))
import nightshift_review_receipts as receipts  # noqa: E402


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
REVIEWER = "nightshift-bot"
PR_URL = "https://github.com/tushardhara/nightshift/pull/999"
COMMENT_URL = PR_URL + "#issuecomment-555"


def make_cx():
    # isolation_level=None matches bin/nightshift-ctl's connect(): autocommit,
    # explicit BEGIN/COMMIT only, exactly what validate_receipt's read helpers
    # assume.
    cx = sqlite3.connect(":memory:", isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.executescript(
        "CREATE TABLE tasks(id TEXT PRIMARY KEY, pr_url TEXT);"
        "CREATE TABLE review_dispatch_markers("
        " task_id TEXT, head_sha TEXT, domain TEXT, marker TEXT,"
        " minted_at REAL, PRIMARY KEY(task_id, head_sha, domain));"
        "CREATE TABLE review_receipts(task_id TEXT, head_sha TEXT, domain TEXT,"
        " marker TEXT, comment_url TEXT);"
    )
    return cx


def seed(cx, *, task_id="t1", pr_url=PR_URL, head_sha=HEAD, domain="qa",
        marker="secret-marker"):
    cx.execute("INSERT INTO tasks(id, pr_url) VALUES(?,?)", (task_id, pr_url))
    cx.execute(
        "INSERT INTO review_dispatch_markers(task_id,head_sha,domain,marker,"
        "minted_at) VALUES(?,?,?,?,0)", (task_id, head_sha, domain, marker))
    return marker


def block(**overrides):
    payload = {
        "domain": "qa", "task_id": "t1", "head_sha": HEAD, "verdict": "pass",
        "marker": "secret-marker",
    }
    payload.update(overrides)
    return payload


def comment_obj(body_block=None, *, created="2026-01-01T00:00:00Z",
                updated=None, issue_url=None, comment_id=555, author=REVIEWER):
    payload = body_block if body_block is not None else block()
    body = "some prose\n\n```nightshift-review\n" + json.dumps(payload) + "\n```\n"
    obj = {
        "created_at": created,
        "updated_at": updated if updated is not None else created,
        "issue_url": issue_url or "https://api.github.com/repos/tushardhara/nightshift/issues/999",
        "body": body,
        "user": {"login": author},
        "author_association": "OWNER" if author == REVIEWER else "NONE",
    }
    if comment_id is not None:
        obj["id"] = comment_id
    return obj


def reviewer_logins(**_):
    return frozenset({REVIEWER})


def fetcher(obj_or_raiser):
    def _fetch(owner, repo, comment_id, *, gh_executable=None):
        if callable(obj_or_raiser) and not isinstance(obj_or_raiser, dict):
            return obj_or_raiser()
        return obj_or_raiser
    return _fetch


class ValidateReceiptHappyPathTest(unittest.TestCase):
    def test_full_pass_with_findings(self):
        cx = make_cx()
        seed(cx)
        result = receipts.validate_receipt(
            cx, task_id="t1", head_sha=HEAD, domain="qa",
            comment_url=COMMENT_URL, reviewer_logins_fn=reviewer_logins,
            fetch_comment_fn=fetcher(comment_obj(block(findings=[
                {"severity": "FOLLOW_UP", "criterion": "AC-9",
                 "summary": "minor nit"},
            ]))))
        self.assertEqual(result["receipt"]["verdict"], "pass")
        self.assertEqual(result["receipt"]["task_id"], "t1")
        self.assertEqual(result["receipt"]["head_sha"], HEAD)
        self.assertEqual(result["receipt"]["domain"], "qa")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["severity"], "FOLLOW_UP")
        self.assertEqual(len(result["receipt"]["report_digest"]), 64)

    def test_persisted_comment_url_is_canonical_not_caller_supplied(self):
        """architect finding on PR #246: the caller's --comment-url is only a
        routing hint for which numeric id to fetch. A decoy owner/repo in
        that string must never end up in the persisted receipt -- only the
        identity re-derived from the FETCHED object may be persisted."""
        cx = make_cx()
        seed(cx)
        decoy_url = "https://github.com/attacker/decoy/pull/1#issuecomment-555"
        result = receipts.validate_receipt(
            cx, task_id="t1", head_sha=HEAD, domain="qa",
            comment_url=decoy_url, fetch_comment_fn=fetcher(comment_obj()),
            reviewer_logins_fn=reviewer_logins)
        self.assertEqual(result["receipt"]["comment_url"], COMMENT_URL)
        self.assertNotIn("attacker", result["receipt"]["comment_url"])
        self.assertNotIn("decoy", result["receipt"]["comment_url"])

    def test_findings_optional(self):
        cx = make_cx()
        seed(cx)
        result = receipts.validate_receipt(
            cx, task_id="t1", head_sha=HEAD, domain="qa",
            comment_url=COMMENT_URL, fetch_comment_fn=fetcher(comment_obj()),
            reviewer_logins_fn=reviewer_logins)
        self.assertEqual(result["findings"], [])


class ValidateReceiptFailClosedTest(unittest.TestCase):
    """One test per AC-3 branch. Every case must raise ReceiptDenied and,
    critically, must not be reachable by adjusting only the fetched comment --
    each asserts the specific reason_code so a future change cannot silently
    swap which check fired."""

    def deny(self, **kwargs):
        cx = kwargs.pop("cx", None) or make_cx()
        if "seeded" not in kwargs or kwargs.pop("seeded"):
            seed(cx)
        fetch = kwargs.pop("fetch", None) or fetcher(comment_obj())
        with self.assertRaises(receipts.ReceiptDenied) as ctx:
            receipts.validate_receipt(
                cx, task_id=kwargs.pop("task_id", "t1"),
                head_sha=kwargs.pop("head_sha", HEAD),
                domain=kwargs.pop("domain", "qa"),
                comment_url=kwargs.pop("comment_url", COMMENT_URL),
                fetch_comment_fn=fetch,
                reviewer_logins_fn=kwargs.pop("logins", reviewer_logins))
        return ctx.exception.reason

    def test_unknown_domain(self):
        self.assertEqual(self.deny(domain="not_a_domain"), "domain_unknown")

    def test_bad_head_sha_shape(self):
        self.assertEqual(self.deny(head_sha="not-40-hex"), "head_sha_invalid")

    def test_task_missing(self):
        self.assertEqual(self.deny(task_id="nope", seeded=False),
                         "task_missing")

    def test_task_pr_url_malformed(self):
        cx = make_cx()
        seed(cx, pr_url="not-a-github-url")
        self.assertEqual(self.deny(cx=cx, seeded=False),
                         "task_pr_url_invalid")

    def test_comment_url_malformed(self):
        self.assertEqual(
            self.deny(comment_url="https://github.com/x/y/pull/1"),
            "comment_url_invalid")

    def test_comment_unreadable(self):
        def boom(*a, **k):
            raise receipts.ReceiptDenied("comment_unreadable")
        self.assertEqual(self.deny(fetch=boom), "comment_unreadable")

    def test_pr_mismatch_wrong_repo_in_fetched_object(self):
        obj = comment_obj(issue_url=(
            "https://api.github.com/repos/someone-else/other-repo/issues/999"))
        self.assertEqual(self.deny(fetch=fetcher(obj)), "pr_mismatch")

    def test_pr_mismatch_wrong_number_in_fetched_object(self):
        obj = comment_obj(issue_url=(
            "https://api.github.com/repos/tushardhara/nightshift/issues/1"))
        self.assertEqual(self.deny(fetch=fetcher(obj)), "pr_mismatch")

    def test_comment_id_mismatch(self):
        """Defense in depth: the fetched object's own id must match what was
        requested, independent of the pr_mismatch check above."""
        obj = comment_obj(comment_id=999999)
        self.assertEqual(self.deny(fetch=fetcher(obj)), "comment_id_mismatch")

    def test_comment_id_missing_from_fetched_object(self):
        obj = comment_obj(comment_id=None)
        self.assertEqual(self.deny(fetch=fetcher(obj)), "comment_id_mismatch")

    def test_comment_edited_fails_closed(self):
        obj = comment_obj(created="2026-01-01T00:00:00Z",
                          updated="2026-01-01T00:05:00Z")
        self.assertEqual(self.deny(fetch=fetcher(obj)), "comment_edited")

    def test_structured_block_missing(self):
        obj = {"id": 555, "created_at": "t", "updated_at": "t",
              "issue_url": "https://api.github.com/repos/tushardhara/nightshift/issues/999",
              "body": "no fenced block here", "user": {"login": REVIEWER}}
        self.assertEqual(self.deny(fetch=fetcher(obj)),
                         "structured_block_malformed")

    def test_structured_block_extra_key_rejected(self):
        payload = block()
        payload["unexpected"] = "surprise"
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "structured_block_malformed")

    def test_structured_block_missing_required_key(self):
        payload = block()
        del payload["verdict"]
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "structured_block_malformed")

    def test_domain_mismatch(self):
        payload = block(domain="security")
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "domain_mismatch")

    def test_task_id_mismatch(self):
        payload = block(task_id="some-other-task")
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "task_id_mismatch")

    def test_head_sha_mismatch(self):
        payload = block(head_sha=OTHER_HEAD)
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "head_sha_mismatch")

    def test_verdict_invalid(self):
        payload = block(verdict="looks-fine-ish")
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "verdict_invalid")

    def test_marker_missing_when_never_minted(self):
        cx = make_cx()
        cx.execute("INSERT INTO tasks(id, pr_url) VALUES(?,?)", ("t1", PR_URL))
        self.assertEqual(self.deny(cx=cx, seeded=False), "marker_missing")

    def test_marker_mismatch(self):
        payload = block(marker="wrong-marker")
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "marker_mismatch")

    def test_stale_marker_after_remint_is_rejected(self):
        """A copy-pasted comment from an earlier dispatch of the SAME tuple
        must not validate once the marker has been re-minted (AC-11's stated
        threat: stale/copy-pasted receipts)."""
        cx = make_cx()
        old_marker = seed(cx, marker="old-marker")
        cx.execute(
            "UPDATE review_dispatch_markers SET marker=? "
            "WHERE task_id='t1' AND head_sha=? AND domain='qa'",
            ("new-marker", HEAD))
        payload = block(marker=old_marker)
        self.assertEqual(self.deny(cx=cx, seeded=False,
                                   fetch=fetcher(comment_obj(payload))),
                         "marker_mismatch")

    def test_findings_bad_severity(self):
        payload = block(findings=[
            {"severity": "SUPER_BLOCKER", "summary": "x"}])
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "findings_malformed")

    def test_findings_empty_summary(self):
        payload = block(findings=[{"severity": "FOLLOW_UP", "summary": "  "}])
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "findings_malformed")

    def test_findings_not_a_list(self):
        payload = block(findings={"severity": "FOLLOW_UP", "summary": "x"})
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "findings_malformed")

    def test_findings_too_many_is_rejected(self):
        """Security FOLLOW_UP (PR #246 round 1): an explicit count cap, not
        just the transitive 1MB whole-comment bound."""
        many = [{"severity": "FOLLOW_UP", "summary": "x"}
               for _ in range(receipts.MAX_FINDINGS_PER_RECEIPT + 1)]
        payload = block(findings=many)
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "findings_malformed")

    def test_findings_summary_too_long_is_rejected(self):
        payload = block(findings=[{
            "severity": "FOLLOW_UP",
            "summary": "x" * (receipts.MAX_SUMMARY_LEN + 1),
        }])
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "findings_malformed")

    def test_findings_criterion_too_long_is_rejected(self):
        payload = block(findings=[{
            "severity": "FOLLOW_UP", "summary": "ok",
            "criterion": "x" * (receipts.MAX_CRITERION_LEN + 1),
        }])
        self.assertEqual(self.deny(fetch=fetcher(comment_obj(payload))),
                         "findings_malformed")


class FingerprintTest(unittest.TestCase):
    def test_excludes_domain_by_construction(self):
        """fingerprint() has no domain parameter at all -- the strongest
        possible proof of AC-7's exclusion, not just an equality check."""
        self.assertNotIn("domain", receipts.fingerprint.__code__.co_varnames)

    def test_normalizes_whitespace_and_case(self):
        a = receipts.fingerprint("AC-3", "Missing   fail-closed check")
        b = receipts.fingerprint("ac-3", "missing fail-closed  check")
        self.assertEqual(a, b)

    def test_different_summary_differs(self):
        a = receipts.fingerprint("AC-3", "one thing")
        b = receipts.fingerprint("AC-3", "a different thing")
        self.assertNotEqual(a, b)


class ConsolidateFindingsTest(unittest.TestCase):
    def test_keeps_highest_severity_on_fingerprint_collision(self):
        fp = receipts.fingerprint("AC-3", "missing fail-closed check")
        rows = [
            {"domain": "code_review", "severity": "PRE_DEPLOYMENT",
             "criterion": "AC-3", "summary": "missing fail-closed check",
             "fingerprint": fp},
            {"domain": "qa", "severity": "MERGE_BLOCKER",
             "criterion": "AC-3", "summary": "missing fail-closed check",
             "fingerprint": fp},
        ]
        out = receipts.consolidate_findings(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["severity"], "MERGE_BLOCKER")
        self.assertEqual(out[0]["domains"], ["code_review", "qa"])

    def test_distinct_fingerprints_stay_separate_and_sorted(self):
        rows = [
            {"domain": "qa", "severity": "FOLLOW_UP", "criterion": "c1",
             "summary": "s1", "fingerprint": "fp-low"},
            {"domain": "security", "severity": "MERGE_BLOCKER",
             "criterion": "c2", "summary": "s2", "fingerprint": "fp-high"},
        ]
        out = receipts.consolidate_findings(rows)
        self.assertEqual([row["severity"] for row in out],
                         ["MERGE_BLOCKER", "FOLLOW_UP"])

    def test_empty_is_empty(self):
        self.assertEqual(receipts.consolidate_findings([]), [])


class ParseRequiredDomainsTest(unittest.TestCase):
    def test_bare_template_has_zero_required_domains(self):
        text = Path(REPO, "docs/reviews/REVIEW-CONTRACT-TEMPLATE.md").read_text(
            encoding="utf-8")
        self.assertEqual(receipts.parse_required_domains(text), [])

    def test_frozen_231_contract_matches_its_own_checklist(self):
        text = Path(
            REPO, "docs/reviews/231-persist-review-receipts-CONTRACT.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            receipts.parse_required_domains(text),
            ["code_review", "cross_crew_review", "pm_acceptance", "qa",
             "security", "staff_adr"])

    def test_unrecognized_label_fails_closed(self):
        text = textwrap.dedent("""
            ## Required review domains
            - [x] Something Nobody Defined
            - [x] PM acceptance
            - [x] Different-vendor code review
            - [x] QA
            - [x] Cross-crew review
            - [x] Staff / ADR consistency
            - [x] Security
            - [x] UX
            ## Next section
        """)
        with self.assertRaises(receipts.ReceiptDenied) as ctx:
            receipts.parse_required_domains(text)
        self.assertEqual(ctx.exception.reason,
                         "contract_domain_label_unrecognized")

    def test_duplicate_label_fails_closed(self):
        text = textwrap.dedent("""
            ## Required review domains
            - [x] QA
            - [ ] QA
            - [x] PM acceptance
            - [x] Different-vendor code review
            - [x] Cross-crew review
            - [x] Staff / ADR consistency
            - [x] Security
            - [x] UX
            ## Next section
        """)
        with self.assertRaises(receipts.ReceiptDenied) as ctx:
            receipts.parse_required_domains(text)
        self.assertEqual(ctx.exception.reason, "contract_domain_label_duplicate")

    def test_missing_canonical_row_fails_closed(self):
        text = textwrap.dedent("""
            ## Required review domains
            - [x] PM acceptance
            - [x] Different-vendor code review
            - [x] QA
            ## Next section
        """)
        with self.assertRaises(receipts.ReceiptDenied) as ctx:
            receipts.parse_required_domains(text)
        self.assertEqual(ctx.exception.reason, "contract_domain_row_missing")

    def test_missing_section_fails_closed(self):
        with self.assertRaises(receipts.ReceiptDenied) as ctx:
            receipts.parse_required_domains("# Nothing relevant here\n")
        self.assertEqual(ctx.exception.reason,
                         "contract_domains_section_missing")

    def test_no_field_named_plan_digest_anywhere(self):
        """AC-8: report_digest, never plan_digest, in this module."""
        source = Path(REPO, "bin/nightshift_review_receipts.py").read_text(
            encoding="utf-8")
        self.assertNotIn("plan_digest", source)
        self.assertIn("report_digest", source)


class ConsolidationIsPureTest(unittest.TestCase):
    """AC-4/AC-5 conformance (staff finding, PR #246 round 1): consolidation
    must never touch GitHub or a subprocess. Static introspection here;
    ReviewReceiptCliTest.test_consolidate_never_shells_out_to_gh proves the
    same thing black-box, by execution, for the actual CLI command."""

    NETWORK_NAMES = {"subprocess", "Popen", "fetch_comment", "_run_bounded",
                     "gh_executable"}

    def test_consolidate_findings_never_references_network_names(self):
        names = set(receipts.consolidate_findings.__code__.co_names)
        self.assertFalse(names & self.NETWORK_NAMES,
                         names & self.NETWORK_NAMES)

    def test_parse_required_domains_never_references_network_names(self):
        names = set(receipts.parse_required_domains.__code__.co_names)
        self.assertFalse(names & self.NETWORK_NAMES,
                         names & self.NETWORK_NAMES)

    def test_module_does_not_import_the_shadow_planner(self):
        """AC-5: consolidation must never reach for #228A's diagnostic-only
        `suggested_domains` (ADR-0036) as if it were authoritative."""
        source = Path(REPO, "bin/nightshift_review_receipts.py").read_text(
            encoding="utf-8")
        self.assertNotIn("import nightshift_review_plan", source)
        self.assertNotIn("suggested_domains", source)


FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    log = os.environ.get("FAKE_GH_CALL_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(" ".join(sys.argv[1:]) + "\\n")
    if sys.argv[1:] == ["api", "--method", "GET", "user"]:
        print(json.dumps({"login": os.environ.get("FAKE_GH_LOGIN", "nightshift-bot")}))
        sys.exit(0)
    path = os.environ["FAKE_GH_RESPONSE_FILE"]
    with open(path, "r", encoding="utf-8") as handle:
        sys.stdout.write(handle.read())
    sys.exit(int(os.environ.get("FAKE_GH_EXIT", "0")))
""")


class ReviewReceiptCliTest(unittest.TestCase):
    """Drives the real binary end to end: schema, marker-mint, receipt-record,
    consolidate. Complements the unit tests above by proving the CLI wiring
    and the DB CHECK constraints, not just the validation function."""

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
        self._env = dict(os.environ)
        self._env["NIGHTSHIFT_CTL_GH"] = self.gh_path
        self._env["FAKE_GH_RESPONSE_FILE"] = self.response_path
        self.call_log_path = os.path.join(self.tmp.name, "gh_calls.log")
        self.addCleanup(os.environ.pop, "NIGHTSHIFT_CTL_GH", None)
        self.addCleanup(os.environ.pop, "FAKE_GH_RESPONSE_FILE", None)
        self.addCleanup(os.environ.pop, "FAKE_GH_CALL_LOG", None)
        os.environ["NIGHTSHIFT_CTL_GH"] = self.gh_path
        os.environ["FAKE_GH_RESPONSE_FILE"] = self.response_path
        os.environ["FAKE_GH_CALL_LOG"] = self.call_log_path

    def gh_was_called(self):
        return os.path.exists(self.call_log_path)

    def mint(self, domain="qa", head_sha=HEAD):
        code, body = self.ctl.run("review", "marker-mint", "--task", "t1",
                                  "--head-sha", head_sha, "--domain", domain)
        self.assertEqual(code, 0, body)
        return body["marker"]

    def write_response(self, obj):
        with open(self.response_path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle)

    def test_consolidate_never_shells_out_to_gh(self):
        """AC-4/AC-5 conformance (staff finding, PR #246 round 1), by
        execution rather than by reading the code: the fake `gh` appends one
        line to FAKE_GH_CALL_LOG every time it runs at all, for any reason.
        `review consolidate` must never produce that file -- not while
        domains are still missing, and not once a receipt exists and it has
        real findings to read back."""
        contract = os.path.join(
            REPO, "docs/reviews/231-persist-review-receipts-CONTRACT.md")

        code, body = self.ctl.run("review", "consolidate", "--task", "t1",
                                  "--head-sha", HEAD, "--contract", contract)
        self.assertEqual(code, 0, body)
        self.assertFalse(body["ready"])
        self.assertFalse(self.gh_was_called(),
                         "review consolidate shelled out to gh while domains "
                         "were still missing")

        marker = self.mint()
        self.write_response(comment_obj(block(marker=marker, findings=[
            {"severity": "PRE_DEPLOYMENT", "criterion": "x", "summary": "y"},
        ])))
        code, body = self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "qa", "--comment-url", COMMENT_URL)
        self.assertEqual(code, 0, body)
        self.assertTrue(self.gh_was_called(),
                        "sanity check: receipt-record should call gh")

        os.remove(self.call_log_path)
        code, body = self.ctl.run("review", "consolidate", "--task", "t1",
                                  "--head-sha", HEAD, "--contract", contract)
        self.assertEqual(code, 0, body)
        self.assertFalse(self.gh_was_called(),
                         "review consolidate shelled out to gh while reading "
                         "back a persisted receipt/finding")

    def test_marker_mint_requires_a_real_task(self):
        code, body = self.ctl.run("review", "marker-mint", "--task", "nope",
                                  "--head-sha", HEAD, "--domain", "qa")
        self.assertEqual(code, 3)
        self.assertEqual(body["reason"], "task_missing")

    def test_record_happy_path_then_consolidate_waits_then_completes(self):
        marker = self.mint()
        self.write_response(comment_obj(block(marker=marker, findings=[
            {"severity": "MERGE_BLOCKER", "criterion": "AC-3",
             "summary": "example blocker"},
        ])))
        code, body = self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "qa", "--comment-url", COMMENT_URL)
        self.assertEqual(code, 0, body)
        self.assertEqual(body["receipt"]["verdict"], "pass")
        self.assertEqual(body["findings_count"], 1)

        contract = os.path.join(REPO,
                                "docs/reviews/231-persist-review-receipts-CONTRACT.md")
        code, body = self.ctl.run("review", "consolidate", "--task", "t1",
                                  "--head-sha", HEAD, "--contract", contract)
        self.assertEqual(code, 0, body)
        self.assertFalse(body["ready"])
        self.assertIn("security", body["missing_domains"])
        self.assertNotIn("qa", body["missing_domains"])

        # Fill in every other required domain so consolidation completes.
        for domain in ("code_review", "cross_crew_review", "pm_acceptance",
                      "staff_adr", "security"):
            marker = self.mint(domain=domain)
            self.write_response(comment_obj(block(domain=domain, marker=marker)))
            code, body = self.ctl.run(
                "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
                "--domain", domain, "--comment-url", COMMENT_URL)
            self.assertEqual(code, 0, body)

        code, body = self.ctl.run("review", "consolidate", "--task", "t1",
                                  "--head-sha", HEAD, "--contract", contract)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ready"])
        self.assertEqual(len(body["findings"]), 1)
        self.assertEqual(body["findings"][0]["severity"], "MERGE_BLOCKER")

    def test_record_fails_closed_on_mismatched_marker_and_writes_nothing(self):
        self.mint()
        self.write_response(comment_obj(block(marker="wrong")))
        code, body = self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "qa", "--comment-url", COMMENT_URL)
        self.assertEqual(code, 3)
        self.assertEqual(body["reason"], "marker_mismatch")

        contract = os.path.join(REPO,
                                "docs/reviews/231-persist-review-receipts-CONTRACT.md")
        code, body = self.ctl.run("review", "consolidate", "--task", "t1",
                                  "--head-sha", HEAD, "--contract", contract)
        self.assertFalse(body["ready"])
        self.assertIn("qa", body["missing_domains"])

    def test_record_fails_closed_on_edited_comment(self):
        marker = self.mint()
        self.write_response(comment_obj(
            block(marker=marker), created="2026-01-01T00:00:00Z",
            updated="2026-01-01T00:05:00Z"))
        code, body = self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "qa", "--comment-url", COMMENT_URL)
        self.assertEqual(code, 3)
        self.assertEqual(body["reason"], "comment_edited")

    def test_recapture_after_remint_replaces_the_receipt_and_its_findings(self):
        marker = self.mint()
        self.write_response(comment_obj(block(marker=marker, findings=[
            {"severity": "FOLLOW_UP", "criterion": "c", "summary": "old"},
        ])))
        code, _ = self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "qa", "--comment-url", COMMENT_URL)
        self.assertEqual(code, 0)

        marker = self.mint()  # re-mint invalidates the old marker
        self.write_response(comment_obj(block(marker=marker, findings=[
            {"severity": "MERGE_BLOCKER", "criterion": "c", "summary": "new"},
        ])))
        code, body = self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "qa", "--comment-url", COMMENT_URL)
        self.assertEqual(code, 0, body)

        with sqlite3.connect(self.ctl.db) as cx:
            cx.row_factory = sqlite3.Row
            receipt_rows = cx.execute(
                "SELECT * FROM review_receipts WHERE task_id='t1'").fetchall()
            finding_rows = cx.execute(
                "SELECT * FROM review_findings WHERE task_id='t1'").fetchall()
        self.assertEqual(len(receipt_rows), 1)
        self.assertEqual(len(finding_rows), 1)
        self.assertEqual(finding_rows[0]["summary"], "new")
        self.assertEqual(finding_rows[0]["severity"], "MERGE_BLOCKER")

    def test_write_transaction_serializes_against_concurrent_remint(self):
        """architect finding on PR #246 (TOCTOU): the fix makes the marker
        recheck atomic with the INSERT by putting both in one BEGIN IMMEDIATE
        transaction. This proves the mechanism that guarantee depends on --
        that a second connection's own BEGIN IMMEDIATE (exactly what `review
        marker-mint` issues) genuinely cannot proceed while this one is open,
        by execution, not by reading the code."""
        self.mint()
        order = []
        barrier = threading.Barrier(2)

        def hold_write_lock():
            cx = sqlite3.connect(self.ctl.db, isolation_level=None, timeout=5)
            cx.execute("BEGIN IMMEDIATE")
            order.append("A-locked")
            barrier.wait()
            time.sleep(0.2)
            cx.execute("COMMIT")
            order.append("A-committed")
            cx.close()

        def try_remint():
            barrier.wait()
            code, _ = self.ctl.run("review", "marker-mint", "--task", "t1",
                                   "--head-sha", HEAD, "--domain", "qa")
            order.append("B-mint-done")
            self.assertEqual(code, 0)

        t1 = threading.Thread(target=hold_write_lock)
        t2 = threading.Thread(target=try_remint)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(order[0], "A-locked")
        self.assertEqual(order[-1], "B-mint-done")
        self.assertLess(order.index("A-committed"), order.index("B-mint-done"),
                        "a concurrent marker-mint proceeded before the "
                        "held BEGIN IMMEDIATE transaction committed")

    def test_domain_check_constraint_is_real(self):
        with sqlite3.connect(self.ctl.db) as cx:
            with self.assertRaises(sqlite3.IntegrityError):
                cx.execute(
                    "INSERT INTO review_receipts(task_id,head_sha,domain,"
                    "verdict,pr_url,comment_url,marker,report_digest,"
                    "comment_created_at,validated_at,created_at) "
                    "VALUES('t1',?,'not_a_real_domain','pass','p','c','m',"
                    "'d','2026-01-01',0,0)", (HEAD,))

    def test_verdict_check_constraint_is_real(self):
        with sqlite3.connect(self.ctl.db) as cx:
            with self.assertRaises(sqlite3.IntegrityError):
                cx.execute(
                    "INSERT INTO review_receipts(task_id,head_sha,domain,"
                    "verdict,pr_url,comment_url,marker,report_digest,"
                    "comment_created_at,validated_at,created_at) "
                    "VALUES('t1',?,'qa','maybe','p','c','m','d',"
                    "'2026-01-01',0,0)", (HEAD,))

    def test_severity_check_constraint_is_real(self):
        with sqlite3.connect(self.ctl.db) as cx:
            with self.assertRaises(sqlite3.IntegrityError):
                cx.execute(
                    "INSERT INTO review_findings(task_id,head_sha,domain,"
                    "severity,fingerprint,summary,created_at) "
                    "VALUES('t1',?,'qa','SUPER_BLOCKER','f','s',0)", (HEAD,))


class ReceiptAuthorIsTheReviewerTest(unittest.TestCase):
    """Audit B2: the marker is public once the real reviewer posts it, so a
    copy of the block in anyone else's comment must not validate."""

    def validate(self, obj, cx=None, logins=reviewer_logins):
        cx = cx or make_cx()
        if cx.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0:
            seed(cx)
        return receipts.validate_receipt(
            cx, task_id="t1", head_sha=HEAD, domain="qa",
            comment_url=COMMENT_URL, fetch_comment_fn=fetcher(obj),
            reviewer_logins_fn=logins)

    def assert_denied(self, reason, obj, **kwargs):
        with self.assertRaises(receipts.ReceiptDenied) as ctx:
            self.validate(obj, **kwargs)
        self.assertEqual(ctx.exception.reason, reason)

    def test_an_outsider_copying_the_marker_with_approve_is_refused(self):
        self.assert_denied("comment_author_untrusted",
                           comment_obj(block(verdict="pass"), author="stranger"))

    def test_a_comment_with_no_author_is_refused(self):
        obj = comment_obj()
        del obj["user"]
        self.assert_denied("comment_author_untrusted", obj)

    def test_login_comparison_ignores_case(self):
        result = self.validate(comment_obj(author=REVIEWER.upper()))
        self.assertEqual(result["receipt"]["verdict"], "pass")

    def test_unresolvable_reviewer_identity_fails_closed(self):
        def unknown(**_):
            raise receipts.ReceiptDenied("reviewer_identity_unknown")
        self.assert_denied("reviewer_identity_unknown", comment_obj(), logins=unknown)

    def test_marker_used_by_another_comment_is_refused(self):
        cx = make_cx()
        seed(cx)
        cx.execute("INSERT INTO review_receipts VALUES('t1',?,'qa',?,?)",
                   (HEAD, "secret-marker", PR_URL + "#issuecomment-554"))
        self.assert_denied("marker_consumed", comment_obj(), cx=cx)

    def test_re_recording_the_same_comment_stays_idempotent(self):
        cx = make_cx()
        seed(cx)
        cx.execute("INSERT INTO review_receipts VALUES('t1',?,'qa',?,?)",
                   (HEAD, "secret-marker", COMMENT_URL))
        self.assertEqual(self.validate(comment_obj(), cx=cx)["receipt"]["marker"],
                         "secret-marker")

    def test_configured_login_overrides_the_authenticated_one(self):
        os.environ[receipts.REVIEWER_LOGIN_ENV] = "Reviewer-A, reviewer-b"
        self.addCleanup(os.environ.pop, receipts.REVIEWER_LOGIN_ENV, None)
        self.assertEqual(receipts.expected_reviewer_logins(),
                         frozenset({"reviewer-a", "reviewer-b"}))
        os.environ[receipts.REVIEWER_LOGIN_ENV] = "not a login"
        with self.assertRaises(receipts.ReceiptDenied) as ctx:
            receipts.expected_reviewer_logins()
        self.assertEqual(ctx.exception.reason, "reviewer_identity_unknown")


class ReceiptAuthorCliTest(unittest.TestCase):
    """The same two refusals through the real binary and the fake `gh`, with
    the expected login coming from `gh api user`."""

    mint = ReviewReceiptCliTest.mint
    write_response = ReviewReceiptCliTest.write_response

    def setUp(self):
        ReviewReceiptCliTest.setUp(self)
        os.environ.pop(receipts.REVIEWER_LOGIN_ENV, None)

    def record(self, comment_id=555):
        return self.ctl.run(
            "review", "receipt-record", "--task", "t1", "--head-sha", HEAD,
            "--domain", "qa", "--comment-url",
            PR_URL + "#issuecomment-%d" % comment_id)

    def receipts_count(self):
        with sqlite3.connect(self.ctl.db) as cx:
            return cx.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0]

    def test_outsider_comment_with_a_copied_marker_persists_nothing(self):
        marker = self.mint()
        self.write_response(comment_obj(block(marker=marker), author="stranger"))
        code, body = self.record()
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "comment_author_untrusted")
        self.assertEqual(self.receipts_count(), 0)
        with open(self.call_log_path, encoding="utf-8") as handle:
            self.assertIn("api --method GET user", handle.read())

    def test_a_consumed_marker_cannot_back_a_second_comment(self):
        marker = self.mint()
        self.write_response(comment_obj(block(marker=marker, verdict="fail")))
        code, body = self.record()
        self.assertEqual(code, 0, body)
        self.write_response(comment_obj(block(marker=marker), comment_id=556))
        code, body = self.record(556)
        self.assertEqual(code, 3, body)
        self.assertEqual(body["reason"], "marker_consumed")
        with sqlite3.connect(self.ctl.db) as cx:
            self.assertEqual(cx.execute(
                "SELECT verdict FROM review_receipts").fetchall(), [("fail",)])


if __name__ == "__main__":
    unittest.main()
