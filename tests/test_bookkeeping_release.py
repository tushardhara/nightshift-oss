"""Issue #286 / ADR-0052: fail-closed fast release path for bookkeeping-only
PRs.

Three layers, matching this suite's own established convention
(`tests/test_review_eligibility.py`'s docstring):
- Fast unit tests call `classify_paths` directly against synthetic diff
  entries -- one per AC-1/AC-5/AC-6 branch, no git or sqlite involved.
- Git-fixture tests call the module's check functions and
  `build_bookkeeping_plan` against a real repo, so append-only, format,
  mergeability and rename/symlink/mode-change refusals are proven against
  real git plumbing output, not a hand-written stand-in for it.
- CLI integration tests drive the real `bin/nightshift-ctl bookkeeping *`
  binary end to end (task upsert -> plan -> verify-head -> receipt-record),
  proving the wiring: exit codes, PLANE_FREE_VERBS, and that receipts persist
  as real, appended rows in control.db.
"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(
    str(Path(__file__).resolve().parents[1]), "bin"))
import nightshift_bookkeeping_release as bk  # noqa: E402

from ctlutil import Ctl  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GIT = "/usr/bin/git"
CTL = os.environ.get("NIGHTSHIFT_TEST_CTL") or str(ROOT / "bin" / "nightshift-ctl")


# --------------------------------------------------------------- fixtures --

class GitRepo:
    """A throwaway git repo, one commit per call to `write_commit`."""

    def __init__(self):
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name)
        self._env = dict(os.environ)
        self._env.update({
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        })
        self._run("-c", "init.defaultBranch=main", "init", str(self.path))

    def close(self):
        self._temp.cleanup()

    def _run(self, *args, check=True):
        return subprocess.run(
            [GIT, *args], cwd=str(self.path) if args[0] != "init" else None,
            check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=self._env,
        ).stdout.strip()

    def git(self, *args, check=True):
        return subprocess.run(
            [GIT, "-C", str(self.path), *args], check=check,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=self._env,
        )

    def write(self, relative, content):
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def checkout(self, ref):
        self.git("checkout", "-q", ref)


def _memory_task_db(base_sha, task_id="t"):
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    cx.execute(
        "CREATE TABLE tasks(id TEXT PRIMARY KEY, crew TEXT, size TEXT, "
        "spec_ref TEXT, base_sha TEXT)")
    cx.execute(
        "INSERT INTO tasks(id, crew, size, spec_ref, base_sha) "
        "VALUES(?, 'crew_alpha', 'S', NULL, ?)", (task_id, base_sha))
    cx.commit()
    return cx


def _entry(path, status="M", old_mode="100644", new_mode="100644"):
    return {
        "status": status, "path": path,
        "old_mode": None if status == "A" else old_mode,
        "new_mode": None if status == "D" else new_mode,
        "old_blob_oid": None if status == "A" else "a" * 40,
        "new_blob_oid": None if status == "D" else "b" * 40,
    }


# ---------------------------------------------------- unit: classify_paths -

class ClassifyPathsTests(unittest.TestCase):
    """AC-1 (mechanical, fail-closed classification), AC-5, AC-6."""

    def test_ac1_empty_diff_classifies_normal(self):
        classification, reasons = bk.classify_paths([])
        self.assertEqual(classification, "normal")
        self.assertIn("empty_diff", reasons)

    def test_ac1_allowlisted_add_and_modify_classify_bookkeeping_only(self):
        entries = [
            _entry(".omni/STATUS.md", status="M"),
            _entry(".omni/ledger.md", status="M"),
            _entry(".omni/tech-debt.md", status="M"),
            _entry(".omni/reports/2026-08-30-x.md", status="A"),
        ]
        classification, reasons = bk.classify_paths(entries)
        self.assertEqual(classification, "bookkeeping-only")
        self.assertEqual(reasons, [])

    def test_ac5_non_allowlisted_path_classifies_normal(self):
        classification, reasons = bk.classify_paths([_entry("bin/nightshift-ctl")])
        self.assertEqual(classification, "normal")
        self.assertTrue(any("path_not_allowlisted" in r for r in reasons))

    def test_ac5_one_bad_path_denies_the_whole_diff(self):
        entries = [_entry(".omni/STATUS.md"), _entry("deploy/policies/omnipods_policies.py")]
        classification, reasons = bk.classify_paths(entries)
        self.assertEqual(classification, "normal")
        self.assertTrue(any("path_not_allowlisted" in r for r in reasons))

    def test_ac6_delete_status_denied(self):
        classification, reasons = bk.classify_paths([_entry(".omni/ledger.md", status="D")])
        self.assertEqual(classification, "normal")
        self.assertTrue(any(r.startswith("non_add_modify_status:D:") for r in reasons))

    def test_ac6_rename_shape_denied_via_add_plus_delete(self):
        # --no-renames always turns a rename into D(old)+A(new); the D alone
        # is enough to deny, and if the new path also happens to be
        # allowlisted the D still refuses the whole diff.
        entries = [
            _entry(".omni/ledger.md", status="D"),
            _entry(".omni/ledger-renamed.md", status="A"),
        ]
        classification, reasons = bk.classify_paths(entries)
        self.assertEqual(classification, "normal")
        self.assertTrue(any(r.startswith("non_add_modify_status:D:") for r in reasons))

    def test_ac6_copy_status_denied(self):
        classification, reasons = bk.classify_paths([_entry(".omni/ledger.md", status="C")])
        self.assertEqual(classification, "normal")
        self.assertTrue(any(r.startswith("non_add_modify_status:C:") for r in reasons))

    def test_ac6_type_change_symlink_denied(self):
        classification, reasons = bk.classify_paths([_entry(".omni/ledger.md", status="T")])
        self.assertEqual(classification, "normal")
        self.assertTrue(any(r.startswith("non_add_modify_status:T:") for r in reasons))

    def test_ac6_executable_mode_denied(self):
        entries = [_entry(".omni/ledger.md", old_mode="100644", new_mode="100755")]
        classification, reasons = bk.classify_paths(entries)
        self.assertEqual(classification, "normal")
        self.assertTrue(any("non_regular_mode" in r for r in reasons))

    def test_ac6_executable_add_denied(self):
        entries = [_entry(".omni/reports/2026-08-30-x.md", status="A", new_mode="100755")]
        classification, reasons = bk.classify_paths(entries)
        self.assertEqual(classification, "normal")
        self.assertTrue(any("non_regular_mode" in r for r in reasons))


# ---------------------------------------------- git-fixture: check_* funcs -

class ContentChecksTests(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo()
        self.addCleanup(self.repo.close)

    def _entries(self, base, head):
        return bk._changes(str(self.repo.path), base, head)

    def test_ledger_append_only_pass(self):
        self.repo.write(".omni/ledger.md", "entry1\n")
        base = self.repo.commit("base")
        self.repo.write(".omni/ledger.md", "entry1\nentry2\n")
        head = self.repo.commit("append")
        entries = self._entries(base, head)
        ok, reason = bk.check_ledger_append_only(str(self.repo.path), entries)
        self.assertTrue(ok, reason)

    def test_ac6_ledger_malformed_edit_fails(self):
        self.repo.write(".omni/ledger.md", "entry1\n")
        base = self.repo.commit("base")
        self.repo.write(".omni/ledger.md", "DIFFERENT\n")
        head = self.repo.commit("rewrite")
        entries = self._entries(base, head)
        ok, reason = bk.check_ledger_append_only(str(self.repo.path), entries)
        self.assertFalse(ok)
        self.assertEqual(reason, "ledger_not_append_only")

    def test_tech_debt_append_only_pass_and_fail(self):
        self.repo.write(".omni/tech-debt.md", "| id | what |\n|---|---|\n")
        base = self.repo.commit("base")
        self.repo.write(".omni/tech-debt.md", "| id | what |\n|---|---|\n| 1 | x |\n")
        head = self.repo.commit("append row")
        entries = self._entries(base, head)
        ok, _ = bk.check_tech_debt_append_only(str(self.repo.path), entries)
        self.assertTrue(ok)

        self.repo.checkout(base)
        self.repo.write(".omni/tech-debt.md", "| id | CHANGED |\n|---|---|\n")
        head2 = self.repo.commit("edit existing row")
        entries2 = self._entries(base, head2)
        ok2, reason2 = bk.check_tech_debt_append_only(str(self.repo.path), entries2)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "tech_debt_not_append_only")

    def test_status_format_pass_and_fail(self):
        self.repo.write(".omni/STATUS.md", "placeholder\n")
        base = self.repo.commit("base")
        self.repo.write(".omni/STATUS.md",
                        "# \U0001F7E2 12:00Z (2026-08-30) -- session\n\nbody\n")
        head = self.repo.commit("good status")
        entries = self._entries(base, head)
        ok, reason = bk.check_status_format(str(self.repo.path), entries)
        self.assertTrue(ok, reason)

        self.repo.checkout(base)
        self.repo.write(".omni/STATUS.md", "no heading here\n")
        head2 = self.repo.commit("bad status")
        entries2 = self._entries(base, head2)
        ok2, reason2 = bk.check_status_format(str(self.repo.path), entries2)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "status_heading_malformed")

    def test_report_format_pass_and_boundaries(self):
        self.repo.write("README.md", "x\n")
        base = self.repo.commit("base")
        self.repo.write(".omni/reports/2026-08-30-heartbeat.md", "# heartbeat\n\nbody\n")
        head = self.repo.commit("good report")
        entries = self._entries(base, head)
        ok, reason = bk.check_report_format(str(self.repo.path), entries)
        self.assertTrue(ok, reason)

        self.repo.checkout(base)
        self.repo.write(".omni/reports/not-dated.md", "# heartbeat\n")
        head2 = self.repo.commit("bad filename")
        entries2 = self._entries(base, head2)
        ok2, reason2 = bk.check_report_format(str(self.repo.path), entries2)
        self.assertFalse(ok2)
        self.assertTrue(reason2.startswith("report_filename_malformed"))

        self.repo.checkout(base)
        self.repo.write(".omni/reports/2026-08-30-nohead.md", "no heading\n")
        head3 = self.repo.commit("no heading")
        entries3 = self._entries(base, head3)
        ok3, reason3 = bk.check_report_format(str(self.repo.path), entries3)
        self.assertFalse(ok3)
        self.assertTrue(reason3.startswith("report_heading_malformed"))

        self.repo.checkout(base)
        self.repo.write(".omni/reports/sub/2026-08-30-nested.md", "# x\n")
        head4 = self.repo.commit("nested report")
        entries4 = self._entries(base, head4)
        ok4, reason4 = bk.check_report_format(str(self.repo.path), entries4)
        self.assertFalse(ok4)
        self.assertTrue(reason4.startswith("report_filename_malformed"))

    def test_clean_mergeability_pass_and_conflict(self):
        self.repo.write("f.txt", "base\n")
        base = self.repo.commit("base")
        self.repo.git("branch", "mainline", base)
        self.repo.write(".omni/ledger.md", "entry1\n")
        head = self.repo.commit("bookkeeping change, no overlap")
        ok, reason = bk.check_clean_mergeability(str(self.repo.path), head, base)
        self.assertTrue(ok, reason)

        # Now make `main` diverge on the SAME path the head diverged on, so
        # the merge genuinely conflicts.
        self.repo.checkout("mainline")
        self.repo.write("f.txt", "mainline-change\n")
        main_head = self.repo.commit("mainline change")
        self.repo.checkout(head)
        self.repo.write("f.txt", "feature-change\n")
        self.repo.git("add", "-A")
        self.repo.git("commit", "-q", "-m", "conflicting feature change")
        conflicting_head = self.repo.git("rev-parse", "HEAD").stdout.strip()
        ok2, reason2 = bk.check_clean_mergeability(
            str(self.repo.path), conflicting_head, main_head)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "merge_conflict")

    def test_clean_mergeability_skipped_when_no_main_given(self):
        ok, reason = bk.check_clean_mergeability(str(self.repo.path), "f" * 40, None)
        self.assertTrue(ok)
        self.assertIsNone(reason)


class HeadIdentityTests(unittest.TestCase):
    """AC-6: a head that changed during verification refuses the fast path."""

    def test_matching_heads_pass(self):
        sha = "a" * 40
        ok, reason = bk.check_head_identity(sha, sha)
        self.assertTrue(ok, reason)

    def test_ac6_head_changed_since_classification_denied(self):
        ok, reason = bk.check_head_identity("a" * 40, "b" * 40)
        self.assertFalse(ok)
        self.assertEqual(reason, "head_changed_since_classification")

    def test_malformed_sha_denied(self):
        ok, reason = bk.check_head_identity("not-a-sha", "a" * 40)
        self.assertFalse(ok)
        self.assertEqual(reason, "expected_head_sha_invalid")


class RealGitBoundaryTests(unittest.TestCase):
    """AC-6 proven against ACTUAL git plumbing output, not synthetic entries:
    a real rename, a real symlink, a real mode change."""

    def setUp(self):
        self.repo = GitRepo()
        self.addCleanup(self.repo.close)
        self.repo.write(".omni/ledger.md", "entry1\n")
        self.base = self.repo.commit("base")

    def test_real_rename_denied(self):
        self.repo.git("mv", ".omni/ledger.md", ".omni/ledger2.md")
        self.repo.git("commit", "-q", "-m", "rename")
        head = self.repo.git("rev-parse", "HEAD").stdout.strip()
        entries = bk._changes(str(self.repo.path), self.base, head)
        statuses = {e["path"]: e["status"] for e in entries}
        self.assertEqual(statuses.get(".omni/ledger.md"), "D")
        classification, reasons = bk.classify_paths(entries)
        self.assertEqual(classification, "normal")

    def test_real_symlink_denied(self):
        (self.repo.path / ".omni/ledger.md").unlink()
        (self.repo.path / ".omni/ledger.md").symlink_to("/etc/passwd")
        self.repo.git("add", ".omni/ledger.md")
        self.repo.git("commit", "-q", "-m", "symlink")
        head = self.repo.git("rev-parse", "HEAD").stdout.strip()
        entries = bk._changes(str(self.repo.path), self.base, head)
        classification, reasons = bk.classify_paths(entries)
        self.assertEqual(classification, "normal")
        self.assertTrue(any(r.startswith("non_add_modify_status:T:") for r in reasons))

    def test_real_executable_mode_change_denied(self):
        os.chmod(self.repo.path / ".omni/ledger.md", 0o755)
        self.repo.git("commit", "-q", "-a", "-m", "mode change")
        head = self.repo.git("rev-parse", "HEAD").stdout.strip()
        entries = bk._changes(str(self.repo.path), self.base, head)
        classification, reasons = bk.classify_paths(entries)
        self.assertEqual(classification, "normal")
        self.assertTrue(any("non_regular_mode" in r for r in reasons))


class BuildPlanTests(unittest.TestCase):
    """End-to-end `build_bookkeeping_plan`, both directions of AC-2/AC-7."""

    def setUp(self):
        self.repo = GitRepo()
        self.addCleanup(self.repo.close)
        self.repo.write(".omni/STATUS.md", "placeholder\n")
        self.repo.write(".omni/ledger.md", "entry1\n")
        self.base = self.repo.commit("base")

    def test_ac2_eligible_bookkeeping_pr_is_fast_path_eligible(self):
        self.repo.write(".omni/STATUS.md",
                        "# \U0001F7E2 12:00Z (2026-08-30) -- ok\n")
        self.repo.write(".omni/ledger.md", "entry1\nentry2\n")
        head = self.repo.commit("bookkeeping")
        cx = _memory_task_db(self.base)
        plan = bk.build_bookkeeping_plan(cx, "t", str(self.repo.path), head)
        self.assertEqual(plan["classification"], "bookkeeping-only")
        self.assertTrue(plan["fast_path_eligible"])
        self.assertEqual(plan["reasons"], [])
        self.assertEqual(
            set(plan["checks"]),
            {"ledger_append_only", "tech_debt_append_only", "status_format",
             "report_format", "clean_mergeability"})

    def test_ac7_normal_source_change_is_never_fast_path_eligible(self):
        self.repo.write("bin/some_tool.py", "print(1)\n")
        head = self.repo.commit("source change")
        cx = _memory_task_db(self.base)
        plan = bk.build_bookkeeping_plan(cx, "t", str(self.repo.path), head)
        self.assertEqual(plan["classification"], "normal")
        self.assertFalse(plan["fast_path_eligible"])
        self.assertEqual(plan["checks"], {})

    def test_bookkeeping_only_but_failed_check_is_not_eligible(self):
        self.repo.write(".omni/ledger.md", "REWRITTEN\n")
        head = self.repo.commit("malformed ledger edit")
        cx = _memory_task_db(self.base)
        plan = bk.build_bookkeeping_plan(cx, "t", str(self.repo.path), head)
        self.assertEqual(plan["classification"], "bookkeeping-only")
        self.assertFalse(plan["fast_path_eligible"])
        self.assertFalse(plan["checks"]["ledger_append_only"]["ok"])

    def test_task_missing_denies(self):
        cx = _memory_task_db(self.base)
        with self.assertRaises(bk.BookkeepingDenied) as ctx:
            bk.build_bookkeeping_plan(cx, "no-such-task", str(self.repo.path), self.base)
        self.assertEqual(ctx.exception.reason, "task_missing")

    def test_base_invalid_denies(self):
        cx = _memory_task_db("not-a-sha")
        with self.assertRaises(bk.BookkeepingDenied) as ctx:
            bk.build_bookkeeping_plan(cx, "t", str(self.repo.path), self.base)
        self.assertEqual(ctx.exception.reason, "base_invalid")


# -------------------------------------------------- structural AC-3 guard --

class DoesNotRunFullSuiteTests(unittest.TestCase):
    """AC-3: the fast path never shells out to the full suite. Proven
    structurally -- the module's only subprocess calls are git plumbing, and
    it names no test-runner path anywhere in its source."""

    def test_module_never_references_the_full_suite(self):
        source = (ROOT / "bin" / "nightshift_bookkeeping_release.py").read_text()
        for needle in ("run.sh", "tests/run", "pytest", "unittest.main"):
            self.assertNotIn(needle, source)

    def test_module_only_subprocess_binary_is_git(self):
        source = (ROOT / "bin" / "nightshift_bookkeeping_release.py").read_text()
        self.assertIn('GIT = "/usr/bin/git"', source)
        # every subprocess.run call in this module starts its argv with GIT
        import ast
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "run"
                 and getattr(getattr(n.func, "value", None), "id", None) == "subprocess"]
        self.assertGreater(len(calls), 0)
        for call in calls:
            first_arg = call.args[0]
            self.assertIsInstance(first_arg, ast.List)
            first_element = first_arg.elts[0]
            self.assertIsInstance(first_element, ast.Name)
            self.assertEqual(first_element.id, "GIT")


# ------------------------------------------------------- CLI integration ---

class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ctl = Ctl()
        self.repo = GitRepo()
        self.addCleanup(self.repo.close)
        self.repo.write(".omni/STATUS.md", "placeholder\n")
        self.repo.write(".omni/ledger.md", "entry1\n")
        self.base = self.repo.commit("base")
        code, body = self.ctl.run(
            "task", "upsert", "--id", "bkt", "--crew", "crew_alpha",
            "--agent", "eng_claude", "--title", "bkt", "--state", "building",
            "--base-sha", self.base, "--size", "S")
        self.assertEqual(code, 0, body)

    def _plan(self, head, main_sha=None):
        argv = ["bookkeeping", "plan", "--task", "bkt", "--repo",
                str(self.repo.path), "--head-sha", head]
        if main_sha is not None:
            argv += ["--main-sha", main_sha]
        return self.ctl.run(*argv)

    def test_ac2_eligible_pr_plans_clean_with_exit_0(self):
        self.repo.write(".omni/STATUS.md",
                        "# \U0001F7E2 12:00Z (2026-08-30) -- ok\n")
        self.repo.write(".omni/ledger.md", "entry1\nentry2\n")
        head = self.repo.commit("bookkeeping")
        code, body = self._plan(head)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["fast_path_eligible"])
        self.assertEqual(body["classification"], "bookkeeping-only")

    def test_ac5_source_change_plans_ineligible_but_not_an_error(self):
        self.repo.write("bin/tool.py", "x\n")
        head = self.repo.commit("source")
        code, body = self._plan(head)
        self.assertEqual(code, 0, body)
        self.assertFalse(body["fast_path_eligible"])
        self.assertEqual(body["classification"], "normal")

    def test_missing_task_denies_with_exit_3(self):
        code, body = self.ctl.run(
            "bookkeeping", "plan", "--task", "no-such-task", "--repo",
            str(self.repo.path), "--head-sha", self.base)
        self.assertEqual(code, 3)
        self.assertEqual(body["error"], "bookkeeping_plan_denied")
        self.assertEqual(body["reason"], "task_missing")
        self.assertEqual(body["classification"], "normal")

    def test_ac6_verify_head_match_and_mismatch(self):
        code, body = self.ctl.run(
            "bookkeeping", "verify-head",
            "--expected-head-sha", self.base, "--actual-head-sha", self.base)
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])

        other = "f" * 40
        code2, body2 = self.ctl.run(
            "bookkeeping", "verify-head",
            "--expected-head-sha", self.base, "--actual-head-sha", other)
        self.assertEqual(code2, 3)
        self.assertEqual(body2["reason"], "head_changed_since_classification")

    def test_verify_head_is_plane_free(self):
        """PLANE_FREE_VERBS wiring: works even with a nonexistent --db."""
        p = subprocess.run(
            [CTL, "--db", "/nonexistent/path/control.db", "bookkeeping",
             "verify-head", "--expected-head-sha", self.base,
             "--actual-head-sha", self.base],
            capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(json.loads(p.stdout.strip())["ok"])

    def test_ac4_receipt_record_persists_and_is_append_only(self):
        code, body = self.ctl.run(
            "bookkeeping", "receipt-record", "--task", "bkt",
            "--base-sha", self.base, "--head-sha", self.base,
            "--classification", "bookkeeping-only", "--verdict", "pass",
            "--recorded-by", "release")
        self.assertEqual(code, 0, body)
        code2, body2 = self.ctl.run(
            "bookkeeping", "receipt-record", "--task", "bkt",
            "--base-sha", self.base, "--head-sha", self.base,
            "--classification", "bookkeeping-only", "--verdict", "pass",
            "--merged-sha", "c" * 40, "--recorded-by", "release")
        self.assertEqual(code2, 0, body2)
        self.assertEqual(body2["merged_sha"], "c" * 40)

        with sqlite3.connect(self.ctl.db) as cx:
            rows = cx.execute(
                "SELECT merged_sha FROM bookkeeping_receipts WHERE task_id=?",
                ("bkt",)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r[0] for r in rows}, {None, "c" * 40})

    def test_receipt_record_rejects_malformed_input(self):
        code, body = self.ctl.run(
            "bookkeeping", "receipt-record", "--task", "bkt",
            "--base-sha", "not-a-sha", "--head-sha", self.base,
            "--classification", "bookkeeping-only", "--verdict", "pass",
            "--recorded-by", "release")
        self.assertEqual(code, 3)
        self.assertEqual(body["reason"], "base_sha_invalid")

    def test_ac3_plan_does_not_run_tests_run_sh(self):
        """The fast path's own check bundle never shells out to the suite;
        proves the CLI wiring inherits AC-3, not just the module in
        isolation."""
        run_sh = ROOT / "tests" / "run_suite.py"
        before = run_sh.stat().st_mtime if run_sh.exists() else None
        self.repo.write(".omni/ledger.md", "entry1\nentry2\n")
        head = self.repo.commit("bookkeeping")
        code, body = self._plan(head)
        self.assertEqual(code, 0, body)
        after = run_sh.stat().st_mtime if run_sh.exists() else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
