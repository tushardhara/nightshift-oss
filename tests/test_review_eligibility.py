"""Issue #232 (#228C) / ADR-0040: review eligibility (direct-or-reused receipts).

Two layers, matching #228A/#231's own convention:
- Fast unit tests call `nightshift_review_eligibility.compute_eligibility`
  directly against an in-repo git fixture and an in-memory-shaped sqlite DB,
  one per AC-1..AC-8 branch.
- A smaller set of CLI integration tests drives the real `bin/nightshift-ctl
  review eligibility` binary against a copied bundle (same bundle-identity
  trick `tests/test_shadow_risk_plan.py` uses), proving the wiring persists
  nothing and reads real rows back through SQLite.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    str(Path(__file__).resolve().parents[1]), "bin"))
import nightshift_review_eligibility as eligibility_module  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GIT = "/usr/bin/git"
SOURCE_FILES = (
    "bin/nightshift-ctl",
    "bin/nightshift_review_plan.py",
    "bin/nightshift_review_receipts.py",
    "bin/nightshift_review_eligibility.py",
    "program/review-risk.json",
)
# Only these three participate in _bundle_rules()'s identity proof (mirrors
# bin/nightshift_review_plan.py's own SOURCE_PATHS) -- the other two files
# just need to be present and importable alongside nightshift-ctl.
IDENTITY_FILES = (
    "bin/nightshift-ctl",
    "bin/nightshift_review_plan.py",
    "program/review-risk.json",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


class EligibilityTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.repo = self.root / "target"
        self.db = self.root / "control.db"
        self._init_repo(self.bundle)
        for relative in SOURCE_FILES:
            target = self.bundle / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        os.chmod(self.bundle / "bin/nightshift-ctl", 0o755)
        self._commit(self.bundle, "exact eligibility bundle")
        self._init_repo(self.repo)
        self._write(self.repo, "README.md", "base\n")
        self.base = self._commit(self.repo, "base")
        self._create_db()

    def _git(self, repo, *args):
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        })
        return subprocess.run(
            [GIT, "-C", str(repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        ).stdout.strip()

    def _init_repo(self, repo):
        repo.mkdir(parents=True)
        subprocess.run(
            [GIT, "-c", "init.defaultBranch=main", "init", str(repo)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def _write(self, repo, relative, content, binary=False):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if binary else content.encode("utf-8"))

    def _commit(self, repo, message):
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", message)
        return self._git(repo, "rev-parse", "HEAD")

    def _head(self, changes, message="head"):
        for path, content in changes.items():
            self._write(self.repo, path, content,
                        binary=isinstance(content, bytes))
        return self._commit(self.repo, message)

    def _create_db(self):
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "CREATE TABLE tasks(id TEXT PRIMARY KEY, crew TEXT, size TEXT, "
                "spec_ref TEXT, base_sha TEXT)")
            cx.execute("CREATE TABLE task_admissions(task_id TEXT, marker TEXT)")
            cx.execute(
                "CREATE TABLE review_receipts(task_id TEXT, head_sha TEXT, "
                "domain TEXT, verdict TEXT, pr_url TEXT, comment_url TEXT, "
                "marker TEXT, report_digest TEXT, comment_created_at TEXT, "
                "validated_at REAL, created_at REAL, rules_digest TEXT, "
                "PRIMARY KEY(task_id, head_sha, domain))")
            cx.execute(
                "CREATE TABLE review_findings(id INTEGER PRIMARY KEY "
                "AUTOINCREMENT, task_id TEXT, head_sha TEXT, domain TEXT, "
                "severity TEXT, fingerprint TEXT, criterion TEXT, "
                "summary TEXT, created_at REAL)")

    def _task(self, task="T-1", crew="crew_alpha", size="M", spec="spec.md",
             base=None):
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "INSERT OR REPLACE INTO tasks(id,crew,size,spec_ref,base_sha) "
                "VALUES(?,?,?,?,?)", (task, crew, size, spec,
                                      self.base if base is None else base))

    def _current_rules_digest(self):
        rules = json.loads((self.bundle / "program/review-risk.json").read_text())
        return hashlib.sha256(canonical(rules).encode()).hexdigest()

    def _receipt(self, *, task="T-1", head, domain, verdict="pass",
                rules_digest="__current__"):
        if rules_digest == "__current__":
            rules_digest = self._current_rules_digest()
        now = 0.0
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "INSERT INTO review_receipts(task_id,head_sha,domain,verdict,"
                "pr_url,comment_url,marker,report_digest,comment_created_at,"
                "validated_at,created_at,rules_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (task, head, domain, verdict,
                 "https://github.com/tushardhara/nightshift/pull/999",
                 "https://github.com/tushardhara/nightshift/pull/999#issuecomment-1",
                 "m", "d", "2026-01-01T00:00:00Z", now, now, rules_digest))

    def _finding(self, *, task="T-1", head, domain, severity="FOLLOW_UP"):
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "INSERT INTO review_findings(task_id,head_sha,domain,severity,"
                "fingerprint,criterion,summary,created_at) "
                "VALUES(?,?,?,?,?,?,?,0)",
                (task, head, domain, severity, "fp", "c", "s"))

    CONTRACT = (
        "## Required review domains\n\n"
        "- [x] PM acceptance\n"
        "- [x] Different-vendor code review\n"
        "- [ ] QA\n"
        "- [x] Cross-crew review\n"
        "- [x] Staff / ADR consistency\n"
        "- [x] Security\n"
        "- [ ] UX\n"
    )

    def _run(self, task, head, *extra):
        command = [
            sys.executable, str(self.bundle / "bin/nightshift-ctl"),
            "--db", str(self.db), "review", "eligibility",
            "--task", task, "--repo", str(self.repo), "--head-sha", head,
            "--contract", str(self.contract_path), *extra,
        ]
        return subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)

    def _result(self, task, head):
        result = self._run(task, head)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)


class EligibilityCliTests(EligibilityTestBase):
    def setUp(self):
        super().setUp()
        self.contract_path = self.root / "CONTRACT.md"
        self.contract_path.write_text(self.CONTRACT, encoding="utf-8")
        self.required = sorted(
            {"pm_acceptance", "code_review", "cross_crew_review",
             "staff_adr", "security"})

    def test_ac8_fresh_task_no_receipts_lists_every_required_domain(self):
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        body = self._result("T-1", head)
        self.assertFalse(body["eligible"])
        self.assertEqual(body["missing_domains"], self.required)
        self.assertEqual(body["domains"], [])

    def test_ac4_direct_pass_no_blocker_satisfies_and_is_deterministic(self):
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=head, domain=domain)
        first = self._result("T-1", head)
        second = self._result("T-1", head)
        self.assertTrue(first["eligible"])
        self.assertEqual(first["missing_domains"], [])
        self.assertEqual(
            sorted(entry["domain"] for entry in first["domains"]), self.required)
        self.assertTrue(all(entry["satisfied_by"] == "direct"
                            for entry in first["domains"]))
        self.assertEqual(first, second)  # AC-5 determinism
        digest_input = dict(first)
        digest_input.pop("eligibility_digest")
        digest_input.pop("ok")
        self.assertEqual(first["eligibility_digest"], hashlib.sha256(
            canonical(digest_input).encode()).hexdigest())

    def test_ac4e_merge_blocker_at_exact_head_is_not_direct_and_falls_to_missing(self):
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=head, domain=domain)
        self._finding(head=head, domain="security", severity="MERGE_BLOCKER")
        body = self._result("T-1", head)
        self.assertFalse(body["eligible"])
        self.assertEqual(body["missing_domains"], ["security"])

    def test_ac4e_pre_deployment_or_follow_up_still_satisfies_directly(self):
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=head, domain=domain)
        self._finding(head=head, domain="security", severity="FOLLOW_UP")
        self._finding(head=head, domain="staff_adr", severity="PRE_DEPLOYMENT")
        body = self._result("T-1", head)
        self.assertTrue(body["eligible"])

    def test_ac2_reuse_across_an_unrelated_change(self):
        # code_review/pm_acceptance/cross_crew_review's covered scope is the
        # FULL changed-path set (AC-2c), so they can only reuse across a
        # truly equivalent diff -- any genuinely new/different diff (even an
        # unrelated addition) correctly invalidates them (missing here, not a
        # bug: proven separately by test_ac7's same-head/all-direct case).
        # security/staff_adr's scope is empty for a docs-only addition, so
        # THEY reuse trivially -- that asymmetry is exactly what this test
        # proves.
        self._task()
        h1 = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=h1, domain=domain)
        h2 = self._head({"docs/unrelated-new-file.md": "new\n"})
        body = self._result("T-1", h2)
        self.assertFalse(body["eligible"])
        self.assertEqual(sorted(body["missing_domains"]),
                         ["code_review", "cross_crew_review", "pm_acceptance"])
        by_domain = {entry["domain"]: entry for entry in body["domains"]}
        self.assertEqual(by_domain["security"]["satisfied_by"], "reused")
        self.assertEqual(by_domain["security"]["head_sha"], h1)
        self.assertEqual(by_domain["staff_adr"]["satisfied_by"], "reused")

    def test_ac2c_in_scope_path_modified_invalidates_reuse(self):
        self._task()
        h1 = self._head({"bin/nightshift-ctl": "v1\n"})
        for domain in self.required:
            self._receipt(head=h1, domain=domain)
        # A second, real change to the SAME control-plane path invalidates
        # staff_adr/security (control_plane scope) reuse, since the blob OID
        # moved -- and code_review/pm_acceptance/cross_crew_review too, since
        # their whole-diff scope changed either way.
        h2 = self._head({"bin/nightshift-ctl": "v2\n"})
        body = self._result("T-1", h2)
        self.assertFalse(body["eligible"])
        self.assertIn("staff_adr", body["missing_domains"])
        self.assertIn("security", body["missing_domains"])

    def test_ac2b_any_finding_severity_blocks_reuse_even_if_content_unchanged(self):
        self._task()
        h1 = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=h1, domain=domain)
        self._finding(head=h1, domain="security", severity="FOLLOW_UP")
        h2 = self._head({"docs/unrelated.md": "new\n"})
        body = self._result("T-1", h2)
        self.assertIn("security", body["missing_domains"])
        # unaffected domains (no findings at h1) still reuse fine
        by_domain = {entry["domain"]: entry for entry in body["domains"]}
        self.assertEqual(by_domain["staff_adr"]["satisfied_by"], "reused")

    def test_ac4b_stale_rules_digest_blocks_direct_satisfaction(self):
        """AC-2(d)/AC-4(b): ruleset drift since the receipt was validated
        invalidates it, direct or reused -- PM-acceptance round-1 finding."""
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=head, domain=domain,
                          rules_digest="stale-pre-drift-digest")
        body = self._result("T-1", head)
        self.assertFalse(body["eligible"])
        self.assertEqual(sorted(body["missing_domains"]), self.required)

    def test_ac2d_stale_rules_digest_blocks_reuse_even_with_vacuous_scope(self):
        self._task()
        h1 = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=h1, domain=domain,
                          rules_digest="stale-pre-drift-digest")
        h2 = self._head({"docs/unrelated.md": "new\n"})
        body = self._result("T-1", h2)
        self.assertFalse(body["eligible"])
        # even security/staff_adr, whose SCOPE at h2 is vacuous, must NOT
        # trivially reuse across a stale (pre-drift) receipt.
        self.assertIn("security", body["missing_domains"])
        self.assertIn("staff_adr", body["missing_domains"])

    def test_null_rules_digest_pre_migration_row_is_treated_as_drift(self):
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        self._receipt(head=head, domain="security", rules_digest=None)
        body = self._result("T-1", head)
        self.assertIn("security", body["missing_domains"])

    def test_matching_rules_digest_is_exposed_and_reproducible(self):
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=head, domain=domain)
        body = self._result("T-1", head)
        self.assertEqual(body["rules_digest"], self._current_rules_digest())

    def test_ac6_unknown_task_fails_closed_with_non_domain_reason(self):
        head = self._head({"docs/x.md": "x\n"})
        result = self._run("no-such-task", head)
        self.assertEqual(result.returncode, 3)
        body = json.loads(result.stdout)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "task_missing")

    def test_ac6_invalid_head_sha_fails_closed(self):
        self._task()
        result = self._run("T-1", "not-a-sha")
        self.assertEqual(result.returncode, 3)
        body = json.loads(result.stdout)
        self.assertEqual(body["reason"], "head_sha_invalid")

    def test_ac7_unchanged_head_matches_an_equivalent_all_direct_run(self):
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=head, domain=domain)
        same_head_result = self._result("T-1", head)
        self.assertTrue(same_head_result["eligible"])
        self.assertTrue(all(entry["satisfied_by"] == "direct"
                            for entry in same_head_result["domains"]))

    def test_no_control_db_writes(self):
        self._task()
        head = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=head, domain=domain)
        before = self.db.read_bytes()
        self._result("T-1", head)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse(Path(str(self.db) + "-wal").exists())

    def test_ac2_in_scope_path_added_invalidates_reuse(self):
        """PM-acceptance round-1/2 gap: explicit ADDED-in-scope-path case
        (modified/renamed already covered by test_ac2c/test_shadow_risk_plan's
        own rename coverage; this is the ADD case specifically)."""
        self._task()
        h1 = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=h1, domain=domain)
        # bin/nightshift-ctl is control_plane -> security AND staff_adr scope.
        h2 = self._head({"bin/nightshift-ctl": "new control-plane file\n"})
        body = self._result("T-1", h2)
        self.assertFalse(body["eligible"])
        self.assertIn("security", body["missing_domains"])
        self.assertIn("staff_adr", body["missing_domains"])

    def test_ac2_in_scope_path_removed_invalidates_reuse(self):
        with_file = self._head({"bin/nightshift-ctl": "v1\n"})
        self._task(base=with_file)  # T-1's base now HAS the control-plane file
        h1 = self._head({"docs/x.md": "x\n"})
        for domain in self.required:
            self._receipt(head=h1, domain=domain)
        removed = self.repo / "bin/nightshift-ctl"
        removed.unlink()
        h2 = self._commit(self.repo, "remove control-plane file")
        body = self._result("T-1", h2)
        self.assertFalse(body["eligible"])
        self.assertIn("security", body["missing_domains"])
        self.assertIn("staff_adr", body["missing_domains"])

    def test_ac4_non_descendant_head_fails_closed(self):
        self._task()
        # A commit NOT reachable from base (a sibling, not a descendant).
        self._git(self.repo, "checkout", "--orphan", "sibling")
        self._write(self.repo, "sibling.md", "not on base's line\n")
        sibling = self._commit(self.repo, "sibling")
        result = self._run("T-1", sibling)
        self.assertEqual(result.returncode, 3)
        body = json.loads(result.stdout)
        self.assertEqual(body["reason"], "head_not_descendant")

    def test_ac4_mixed_direct_and_reused_in_one_call(self):
        self._task()
        h1 = self._head({"bin/nightshift-ctl": "v1\n"})
        for domain in self.required:
            self._receipt(head=h1, domain=domain)
        # h2: unrelated docs change (security/staff_adr scope stays vacuous,
        # so they REUSE from h1); pm_acceptance gets a fresh DIRECT receipt
        # at h2 itself.
        h2 = self._head({"docs/unrelated.md": "new\n"})
        self._receipt(head=h2, domain="pm_acceptance")
        body = self._result("T-1", h2)
        by_domain = {entry["domain"]: entry for entry in body["domains"]}
        self.assertEqual(by_domain["pm_acceptance"]["satisfied_by"], "direct")
        self.assertEqual(by_domain["security"]["satisfied_by"], "reused")
        self.assertEqual(by_domain["staff_adr"]["satisfied_by"], "reused")
        # code_review/cross_crew_review still can't reuse (whole-diff scope,
        # h1 != h2's full diff) and got no fresh receipt at h2 -> missing.
        self.assertEqual(sorted(body["missing_domains"]),
                         ["code_review", "cross_crew_review"])

    # -- Issue #277 / ADR-0051, Required regression proof #7 --------------
    #
    # "A fix that changes only a subset of reviewed bytes invalidates/reruns
    # only causally affected evidence; changed criteria/head/provenance
    # still invalidate the relevant gates."
    #
    # Every existing full-vs-partial test above changes exactly ONE path at
    # a time (test_ac2c/test_ac2_in_scope_path_added/removed) or an entirely
    # UNRELATED path (test_ac2_reuse_across_an_unrelated_change,
    # test_ac4_mixed_direct_and_reused_in_one_call) -- none of them puts two
    # REVIEWED, DIFFERENTLY-SCOPED paths in the SAME original diff and then
    # fixes only one of the two. That is the literal "partial fix, subset of
    # reviewed bytes" scenario proof #7 names, and it is the one shape that
    # could reveal a granularity bug: if eligibility ever collapsed to
    # "any byte anywhere in the diff changed -> invalidate everything",
    # `staff_adr` below would wrongly go missing even though its own
    # covered-scope path never moved.
    #
    # `security`'s covered scope is {control_plane, workflow, security_path}
    # paths; `staff_adr`'s is {control_plane, constitution, schema_migration,
    # dependency, public_contract} paths (bin/nightshift_review_eligibility.py
    # `_HIGH_RISK_TO_DOMAIN`). `bin/nightshift-ctl` is control_plane (in BOTH
    # domains' scope); `auth/login.py` is security_path (in security's scope
    # ONLY). A diff touching both, followed by a fix touching only
    # `auth/login.py`, must invalidate `security` (its reviewed bytes moved)
    # while `staff_adr` reuses (its reviewed bytes -- `bin/nightshift-ctl` --
    # never moved), even though the *overall* diff between h1 and h2 is
    # different both times and the whole-diff domains are invalidated either
    # way. A follow-up full fix (both paths change) then invalidates BOTH,
    # proving this is genuinely a full-vs-partial distinction and not just
    # "staff_adr always reuses."
    def test_ac7_byte_subset_partial_fix_invalidates_only_the_touched_domain(self):
        self._task()
        h1 = self._head({
            "bin/nightshift-ctl": "control-plane v1\n",
            "auth/login.py": "security-path v1\n",
        })
        for domain in self.required:
            self._receipt(head=h1, domain=domain)

        # Partial fix: touches ONLY the security-scoped path. The
        # control-plane byte range staff_adr actually reviewed is untouched.
        h2_partial = self._head({"auth/login.py": "security-path v2 -- fixed\n"})
        partial = self._result("T-1", h2_partial)
        self.assertFalse(partial["eligible"])
        self.assertIn("security", partial["missing_domains"],
                      "security's reviewed bytes changed but it was not "
                      "invalidated -- partial fix over-reused (proof #7).")
        self.assertNotIn("staff_adr", partial["missing_domains"],
                         "staff_adr's reviewed bytes (bin/nightshift-ctl) "
                         "never changed, but it was invalidated anyway -- "
                         "partial fix over-invalidated unrelated evidence "
                         "(proof #7).")
        by_domain = {entry["domain"]: entry for entry in partial["domains"]}
        self.assertEqual(by_domain["staff_adr"]["satisfied_by"], "reused")
        self.assertEqual(by_domain["staff_adr"]["head_sha"], h1)

        # Contrast: a full fix touching BOTH previously-reviewed paths
        # invalidates both -- this is not "staff_adr is simply exempt".
        self._task(base=h1)
        h2_full = self._head({
            "bin/nightshift-ctl": "control-plane v2 -- fixed\n",
            "auth/login.py": "security-path v2 -- fixed\n",
        })
        full = self._result("T-1", h2_full)
        self.assertFalse(full["eligible"])
        self.assertIn("security", full["missing_domains"])
        self.assertIn("staff_adr", full["missing_domains"],
                      "a fix touching staff_adr's own reviewed path did not "
                      "invalidate it -- full invalidation is broken "
                      "(proof #7 contrast case).")


class EligibilityPurityTests(unittest.TestCase):
    """AC-3 conformance, mirroring #231's ConsolidationIsPureTest: eligibility
    must never touch GitHub or a subprocess-shaped network call."""

    NETWORK_NAMES = {"subprocess", "Popen", "fetch_comment", "_run_bounded",
                     "gh_executable"}

    def test_compute_eligibility_never_references_network_names(self):
        names = set(eligibility_module.compute_eligibility.__code__.co_names)
        self.assertFalse(names & self.NETWORK_NAMES, names & self.NETWORK_NAMES)

    def test_module_source_has_no_network_names(self):
        source = Path(ROOT, "bin/nightshift_review_eligibility.py").read_text(
            encoding="utf-8")
        for name in self.NETWORK_NAMES:
            self.assertNotIn(name, source, name)


if __name__ == "__main__":
    unittest.main()
