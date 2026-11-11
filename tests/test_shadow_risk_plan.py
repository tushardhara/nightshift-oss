"""Focused black-box contract tests for ADR-0036's shadow planner."""

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


ROOT = Path(__file__).resolve().parents[1]
GIT = "/usr/bin/git"
SOURCE_FILES = (
    "bin/nightshift-ctl",
    "bin/nightshift_review_plan.py",
    "program/review-risk.json",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


class ShadowRiskPlanTests(unittest.TestCase):
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
        self._commit(self.bundle, "exact planner bundle")
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

    def _create_db(self):
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "CREATE TABLE tasks(id TEXT PRIMARY KEY, crew TEXT, size TEXT, "
                "spec_ref TEXT, base_sha TEXT)"
            )
            cx.execute("CREATE TABLE task_admissions(task_id TEXT, marker TEXT)")
            cx.execute("INSERT INTO task_admissions VALUES('sentinel','unchanged')")

    def _task(self, task="T-1", crew="program", size="S", spec="spec.md",
              base=None):
        with sqlite3.connect(self.db) as cx:
            cx.execute(
                "INSERT OR REPLACE INTO tasks(id,crew,size,spec_ref,base_sha) "
                "VALUES(?,?,?,?,?)", (task, crew, size, spec,
                                      self.base if base is None else base),
            )

    def _head(self, changes, message="head"):
        for path, content in changes.items():
            self._write(self.repo, path, content,
                        binary=isinstance(content, bytes))
        return self._commit(self.repo, message)

    def _run(self, head, *extra, db=None):
        command = [
            sys.executable, str(self.bundle / "bin/nightshift-ctl"),
            "--db", str(self.db if db is None else db), "review", "plan",
            "--task", "T-1", "--repo", str(self.repo), "--head-sha", head,
            *extra,
        ]
        return subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)

    def _report(self, head):
        result = self._run(head)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout), result.stdout

    def test_fast_is_canonical_exact_and_has_no_side_effects(self):
        self._task()
        head = self._head({"docs/guide.md": "human guide\n"})
        before_db = self.db.read_bytes()
        before_ref = self._git(self.repo, "show-ref")
        before_status = self._git(self.repo, "status", "--porcelain=v1")
        before_bundle = sorted(str(path.relative_to(self.bundle))
                               for path in self.bundle.rglob("*") if path.is_file())

        report, first = self._report(head)
        again, second = self._report(head)

        self.assertEqual(first, second)
        self.assertEqual(report, again)
        self.assertEqual(first, canonical(report) + "\n")
        self.assertEqual(report["authority"], "shadow_only")
        self.assertEqual(report["classification"]["profile"], "FAST")
        self.assertEqual(report["suggested_domains"], ["code_review", "pm"])
        self.assertEqual(report["suggested_plan"]["human_review"], {
            "domains": ["code_review", "pm"], "mode": "parallel", "wave": 1,
        })
        self.assertEqual(report["suggested_plan"]["gates"], [
            {"gate": "deterministic", "wave": 1},
            {"gate": "exact_merge_result", "wave": 2, "after": [1]},
        ])
        digest_input = dict(report)
        digest_input.pop("report_digest")
        self.assertEqual(report["report_digest"], hashlib.sha256(
            canonical(digest_input).encode()).hexdigest())
        for key in ("bundle_sha", "controller_blob_oid", "planner_blob_oid",
                    "rules_blob_oid"):
            self.assertRegex(report["rules"][key], r"^[0-9a-f]{40}$")
        self.assertEqual(report["rules"]["bundle_sha"],
                         self._git(self.bundle, "rev-parse", "HEAD"))
        self.assertEqual(report["rules"]["rules_blob_oid"], self._git(
            self.bundle, "rev-parse", "HEAD:program/review-risk.json"))
        rules = json.loads((self.bundle / "program/review-risk.json").read_text())
        self.assertEqual(report["rules"]["rules_digest"], hashlib.sha256(
            canonical(rules).encode()).hexdigest())
        plan = dict(report["suggested_plan"])
        plan_digest = plan.pop("plan_digest")
        self.assertEqual(plan_digest,
                         hashlib.sha256(canonical(plan).encode()).hexdigest())
        self.assertEqual(report["target"]["base_sha"], self.base)
        self.assertEqual(report["target"]["head_sha"], head)
        self.assertEqual(report["target"]["changed_manifest_digest"],
                         hashlib.sha256(canonical(
                             report["target"]["changes"]).encode()).hexdigest())
        self.assertEqual(self.db.read_bytes(), before_db)
        self.assertFalse(Path(str(self.db) + "-wal").exists())
        self.assertEqual(self._git(self.repo, "show-ref"), before_ref)
        self.assertEqual(self._git(self.repo, "status", "--porcelain=v1"),
                         before_status)
        self.assertEqual(sorted(str(path.relative_to(self.bundle))
                                for path in self.bundle.rglob("*")
                                if path.is_file()), before_bundle)
        with sqlite3.connect(self.db) as cx:
            self.assertEqual(cx.execute("SELECT * FROM task_admissions").fetchall(),
                             [("sentinel", "unchanged")])

    def test_frozen_standard_and_high_risk_fixtures(self):
        self._task(crew="crew_beta", size="M")
        standard = self._head({"deploy/lib/widget.txt": "ordinary change\n"})
        report, _ = self._report(standard)
        self.assertEqual(report["classification"]["profile"], "STANDARD")
        self.assertEqual(report["suggested_domains"],
                         ["code_review", "pm", "qa"])

        self._task(crew="program", size="M", base=standard)
        ordinary = self._head({"docs/ordinary.md": "ordinary\n"})
        report, _ = self._report(ordinary)
        self.assertEqual(report["classification"]["profile"], "STANDARD")
        self.assertEqual(report["suggested_domains"], ["code_review", "pm"])

        self._task(crew="platform", size="S", base=ordinary)
        high = self._head({".github/workflows/check.yml": "name: check\n"})
        report, _ = self._report(high)
        self.assertEqual(report["classification"]["profile"], "HIGH_RISK")
        self.assertIn("security", report["suggested_domains"])
        self.assertIn({"kind": "protected_path", "group": "workflow",
                       "path": ".github/workflows/check.yml", "side": "new"},
                      report["classification"]["triggers"])

        self._task(crew="crew_alpha", size="S", base=high)
        control = self._head({"bin/nightshift-ctl": "ordinary\n"})
        report, _ = self._report(control)
        self.assertEqual(report["classification"]["profile"], "HIGH_RISK")
        self.assertIn("staff", report["suggested_domains"])

    def test_rename_evaluates_old_and_new_paths_and_blobs(self):
        old_text = "\n".join(["ordinary line"] * 20 + ["token = value"]) + "\n"
        self._write(self.repo, "auth/token.py", old_text)
        old_base = self._commit(self.repo, "sensitive base")
        self._task(base=old_base)
        (self.repo / "docs").mkdir(exist_ok=True)
        os.rename(self.repo / "auth/token.py", self.repo / "docs/safe.md")
        self._write(self.repo, "docs/safe.md",
                    old_text.replace("token = value", "password = value"))
        head = self._commit(self.repo, "rename")

        report, _ = self._report(head)

        self.assertEqual(report["classification"]["profile"], "HIGH_RISK")
        change = report["target"]["changes"][0]
        self.assertEqual((change["old_path"], change["new_path"]),
                         ("auth/token.py", "docs/safe.md"))
        self.assertRegex(change["old_blob_oid"], r"^[0-9a-f]{40}$")
        self.assertRegex(change["new_blob_oid"], r"^[0-9a-f]{40}$")
        self.assertNotEqual(change["old_blob_oid"], change["new_blob_oid"])
        sensitive = [item for item in report["classification"]["triggers"]
                     if item["kind"] == "sensitive_content"]
        self.assertEqual({(item["side"], item["path"]) for item in sensitive},
                         {("old", "auth/token.py"),
                          ("new", "docs/safe.md")})

    def test_valid_authority_with_ambiguous_evidence_succeeds_high(self):
        self._task()
        head = self._head({"mystery/data.bin": b"\x00\xff\x00"})
        report, _ = self._report(head)
        self.assertEqual(report["classification"]["profile"], "HIGH_RISK")
        self.assertTrue(report["classification"]["ambiguities"])

    def test_ux_foreign_owner_and_large_size_are_conservative(self):
        self._task(crew="program", size="S")
        ux_head = self._head({"docs/HUMAN-COMMUNICATION.md": "clear copy\n"})
        report, _ = self._report(ux_head)
        self.assertEqual(report["classification"]["profile"], "STANDARD")
        self.assertIn("ux", report["suggested_domains"])

        self._task(crew="crew_alpha", size="S", base=ux_head)
        foreign = self._head({"deploy/lib/foreign.txt": "change\n"})
        report, _ = self._report(foreign)
        self.assertEqual(report["classification"]["profile"], "HIGH_RISK")
        self.assertIn("cross_crew", report["suggested_domains"])
        self.assertTrue(any(item["kind"] == "other_owner"
                            for item in report["classification"]["triggers"]))

        self._task(crew="program", size="L", base=foreign)
        large = self._head({"docs/large.md": "large task\n"})
        report, _ = self._report(large)
        self.assertEqual(report["classification"]["profile"], "HIGH_RISK")
        self.assertIn("task_size_missing_or_large",
                      report["classification"]["ambiguities"])

    def test_target_rules_change_or_delete_cannot_select_bundle_rules(self):
        self._write(self.repo, "program/review-risk.json", "base target bytes\n")
        target_base = self._commit(self.repo, "target rules base")
        self._task(crew="platform", size="S", base=target_base)
        self._write(self.repo, "program/review-risk.json", "changed target bytes\n")
        changed = self._commit(self.repo, "change target rules")
        changed_report, _ = self._report(changed)

        self._git(self.repo, "checkout", "-B", "delete-case", target_base)
        (self.repo / "program/review-risk.json").unlink()
        deleted = self._commit(self.repo, "delete target rules")
        deleted_report, _ = self._report(deleted)

        self.assertEqual(changed_report["rules"], deleted_report["rules"])
        self.assertEqual(changed_report["classification"]["profile"],
                         "HIGH_RISK")
        self.assertEqual(deleted_report["classification"]["profile"],
                         "HIGH_RISK")
        self.assertEqual(changed_report["rules"]["rules_blob_oid"], self._git(
            self.bundle, "rev-parse", "HEAD:program/review-risk.json"))

    def test_missing_db_and_task_exit_three_without_plan(self):
        head = self._head({"docs/guide.md": "guide\n"})
        missing = self._run(head, db=self.root / "missing.db")
        self.assertEqual(missing.returncode, 3)
        self.assertNotIn("suggested_plan", json.loads(missing.stdout))
        no_task = self._run(head)
        self.assertEqual(no_task.returncode, 3)
        self.assertEqual(json.loads(no_task.stdout)["reason"], "task_missing")

    def test_normal_db_discovery_is_independent_of_target_repo(self):
        self._task()
        head = self._head({"docs/guide.md": "guide\n"})
        discovered = self.bundle / ".omni/control.db"
        discovered.parent.mkdir()
        shutil.copy2(self.db, discovered)
        command = [
            sys.executable, str(self.bundle / "bin/nightshift-ctl"),
            "review", "plan", "--task", "T-1", "--repo", str(self.repo),
            "--head-sha", head,
        ]
        result = subprocess.run(command, cwd=self.bundle / "bin",
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["task"]["id"], "T-1")
        self.assertFalse((self.repo / ".omni/control.db").exists())

    def test_invalid_base_and_head_exit_three(self):
        self._task(base="bad")
        head = self._head({"docs/guide.md": "guide\n"})
        self.assertEqual(self._run(head).returncode, 3)
        self._task(base=self.base)
        self.assertEqual(self._run(head[:12]).returncode, 3)

    def test_review_plan_may_leave_wal_and_shm_on_a_real_wal_mode_db(self):
        # ADR-0038 supersedes ADR-0036 for this one claim (ADR-0036's own
        # text is unedited; see its "Superseded-by" backlink) after
        # `architect` proved, by execution, that suppressing `-wal`/`-shm`
        # sidecar creation via `immutable=1` causes a WORSE defect: it
        # silently hides committed-but-uncheckpointed data (see the two
        # tests below). The corrected guarantee (ADR-0038) is "no
        # control-plane DATA mutation, no write transaction, no schema
        # change, no silent staleness" -- NOT "no sidecar file ever
        # touched". This test is crew_beta's ORIGINAL no-prior-writer repro,
        # kept to prove that shape still behaves sanely: a real WAL-mode DB
        # (via the actual `nightshift-ctl init` path), no prior writer
        # session open, `review plan` still succeeds and returns the right
        # task -- sidecar files may now appear again, and that is expected,
        # not a regression.
        wal_db = self.root / "wal_control.db"
        init_result = subprocess.run(
            [sys.executable, str(self.bundle / "bin/nightshift-ctl"),
             "--db", str(wal_db), "init"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(init_result.returncode, 0,
                         init_result.stderr + init_result.stdout)
        upsert_result = subprocess.run(
            [sys.executable, str(self.bundle / "bin/nightshift-ctl"),
             "--db", str(wal_db), "task", "upsert", "--id", "T-1",
             "--crew", "program", "--size", "S", "--spec-ref", "spec.md",
             "--base-sha", self.base],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(upsert_result.returncode, 0,
                         upsert_result.stderr + upsert_result.stdout)
        # Each writer above is a separate subprocess that has fully closed,
        # so this is the "no prior writer session open" starting state
        # crew_beta's repro requires -- not a mid-transaction snapshot.
        result = self._run(self.base, db=wal_db)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["task"]["id"], "T-1")
        # Deliberately NOT asserting `-wal`/`-shm` absence here -- ordinary
        # SQLite behaviour on a WAL-mode database, and no longer a defect.

    def _start_uncheckpointed_writer(self, db_path, task_id, base_sha):
        """Open a writer connection, COMMIT one row, and hold the connection
        open without checkpointing -- reproduces `architect`'s exact repro
        shape (a row committed by a concurrent writer but not yet
        checkpointed into the base file). Returns the live subprocess;
        caller must terminate() it. Synchronizes on a "READY" line so the
        assertion below never races the commit.
        """
        script = (
            "import sqlite3, sys, time\n"
            "cx = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
            "cx.execute('BEGIN')\n"
            "cx.execute(\n"
            "    \"INSERT INTO tasks(id,crew,size,spec_ref,base_sha,state,\"\n"
            "    \"created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)\",\n"
            "    (sys.argv[2], 'program', 'S', 'spec.md', sys.argv[3],\n"
            "     'pending', time.time(), time.time()),\n"
            ")\n"
            "cx.execute('COMMIT')\n"
            "print('READY', flush=True)\n"
            "time.sleep(30)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(db_path), task_id, base_sha],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        line = proc.stdout.readline()
        self.assertEqual(line.strip(), "READY",
                         f"writer never reported ready: {line!r} / "
                         f"{proc.stderr.read() if proc.poll() is not None else ''}")

        def _terminate():
            proc.kill()
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()

        self.addCleanup(_terminate)
        return proc

    def test_review_plan_sees_a_committed_but_uncheckpointed_row(self):
        # ADR-0038 Acceptance boundary condition 2. Regression for the
        # WORSE defect the reverted `immutable=1` fix introduced:
        # `architect` proved that connection option makes SQLite treat the
        # file's contents as fixed at open time, so it never consults the
        # live WAL and silently misses a row committed by a concurrent
        # writer that has not yet been checkpointed into the base file.
        # `review plan` must never do that -- the control plane is under
        # continuous concurrent write load in production.
        wal_db = self.root / "wal_control_live.db"
        init_result = subprocess.run(
            [sys.executable, str(self.bundle / "bin/nightshift-ctl"),
             "--db", str(wal_db), "init"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(init_result.returncode, 0,
                         init_result.stderr + init_result.stdout)
        self._start_uncheckpointed_writer(wal_db, "T-LIVE", self.base)
        result = self._run(self.base, "--task", "T-LIVE", db=wal_db)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["task"]["id"], "T-LIVE")

    def test_project_snapshot_sees_a_committed_but_uncheckpointed_row(self):
        # ADR-0038 Acceptance boundary condition 2 (project-snapshot side).
        wal_db = self.root / "wal_control_live2.db"
        init_result = subprocess.run(
            [sys.executable, str(self.bundle / "bin/nightshift-ctl"),
             "--db", str(wal_db), "init"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(init_result.returncode, 0,
                         init_result.stderr + init_result.stdout)
        self._start_uncheckpointed_writer(wal_db, "T-LIVE2", self.base)
        snapshot = subprocess.run(
            [sys.executable, str(self.bundle / "bin/nightshift-ctl"),
             "--db", str(wal_db), "project-snapshot"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(snapshot.returncode, 0,
                         snapshot.stderr + snapshot.stdout)
        seen = [row["id"] for row in json.loads(snapshot.stdout)["tasks"]]
        self.assertIn("T-LIVE2", seen)

    def test_review_plan_never_mutates_the_base_db_file_bytes(self):
        # ADR-0038 Acceptance boundary condition 3. The read-only
        # connection's guarantee is on the base `.db` file's own bytes
        # (opened O_RDONLY at the SQLite/OS level, so this connection is
        # physically incapable of writing to it) -- not on whether SQLite's
        # separate `-wal`/`-shm` sidecar files exist or change size, which
        # ordinary WAL-mode operation may do.
        wal_db = self.root / "wal_control_bytes.db"
        init_result = subprocess.run(
            [sys.executable, str(self.bundle / "bin/nightshift-ctl"),
             "--db", str(wal_db), "init"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(init_result.returncode, 0,
                         init_result.stderr + init_result.stdout)
        upsert_result = subprocess.run(
            [sys.executable, str(self.bundle / "bin/nightshift-ctl"),
             "--db", str(wal_db), "task", "upsert", "--id", "T-1",
             "--crew", "program", "--size", "S", "--spec-ref", "spec.md",
             "--base-sha", self.base],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(upsert_result.returncode, 0,
                         upsert_result.stderr + upsert_result.stdout)
        before = wal_db.read_bytes()
        result = self._run(self.base, db=wal_db)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(wal_db.read_bytes(), before,
                         "review plan (read-only) mutated the base .db "
                         "file's own bytes")

    def test_missing_schema_is_read_only_and_exits_three(self):
        empty = self.root / "empty.db"
        with sqlite3.connect(empty):
            pass
        before = empty.read_bytes()
        head = self._head({"docs/guide.md": "guide\n"})
        result = self._run(head, db=empty)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "control_db_unreadable")
        self.assertEqual(empty.read_bytes(), before)
        self.assertFalse(Path(str(empty) + "-wal").exists())

    def test_rules_malformed_or_worktree_modified_fail_closed(self):
        self._task()
        head = self._head({"docs/guide.md": "guide\n"})
        rules_path = self.bundle / "program/review-risk.json"
        rules_path.write_text("{}\n")
        dirty = self._run(head)
        self.assertEqual(dirty.returncode, 3)
        self.assertEqual(json.loads(dirty.stdout)["reason"],
                         "bundle_identity_unproved")
        self._commit(self.bundle, "malformed rules")
        malformed = self._run(head)
        self.assertEqual(malformed.returncode, 3)
        self.assertEqual(json.loads(malformed.stdout)["reason"], "rules_invalid")
        (self.bundle / "bin/nightshift_review_plan.py").unlink()
        missing_module = self._run(head)
        self.assertEqual(missing_module.returncode, 3)
        self.assertEqual(json.loads(missing_module.stdout)["reason"],
                         "bundle_identity_unproved")

    def test_cli_has_no_downgrade_arguments(self):
        self._task()
        head = self._head({"docs/guide.md": "guide\n"})
        for option in ("--base-sha", "--profile", "--domain", "--rules"):
            result = self._run(head, option, "FAST")
            self.assertEqual(result.returncode, 2, option)

    def test_no_authorizing_subsystem_imports_shadow_planner(self):
        # ADR-0042 (accepted) narrowly authorizes exactly two files to consult
        # `review plan`'s `suggested_domains` -- dispatch narrowing only, never
        # merge eligibility (which stays on `review eligibility`, never this
        # planner -- see test_review_eligibility.py's own boundary test, if
        # present, and ADR-0040's frozen contract). Every other file under
        # .github/, deploy/, and program/ must still never reference it.
        # Issue #720 / ADR-0091 adds two more, and narrows what the exemption
        # buys. ADR-0091 supersedes ADR-0042 on convention-versus-machinery:
        # the mint (`bin/nightshift-ctl`, not scanned here) now consults the
        # planner for three seats. Neither file below does. One carries the
        # fence's denial message, the other Program's instruction for how to
        # mint -- both prose about the planner, not a caller of it. The
        # assertion after this loop is what keeps that distinction real: an
        # exempt file may NAME the planner and still may not IMPORT it, so
        # this list cannot quietly become a way for an authorizing subsystem
        # to acquire the planner by being added to it.
        exempt = {
            ROOT / "program/review-risk.json",
            ROOT / "program/agents/crew_alpha/config.yaml",
            ROOT / "program/agents/crew_beta/config.yaml",
            ROOT / "deploy/policies/omnipods_policies.py",
            ROOT / "program/config.yaml",
        }
        affected = [ROOT / ".github", ROOT / "deploy", ROOT / "program"]
        hits = []
        for directory in affected:
            for path in directory.rglob("*"):
                if path.is_file() and path not in exempt:
                    try:
                        text = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        continue
                    if "nightshift_review_plan" in text or "review plan" in text:
                        hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [])
        # The exemption is to NAME the planner, never to import it. Checked
        # rather than asserted in a comment, because an allowlist whose entries
        # are unconstrained is a hole with a list of names on it.
        for path in exempt:
            if not path.is_file() or path.suffix != ".py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("import nightshift_review_plan", text,
                             f"{path.name} is exempt from naming the planner, "
                             "not from ADR-0036's rule against importing it")

    # -- Issue #277 / ADR-0051, Required regression proof #2 --------------
    #
    # #228's Definition of finished names this explicitly: "STANDARD work
    # runs independent eligible domains concurrently rather than serially."
    # The *actual* concurrent dispatch is an LLM crew lead emitting several
    # sys_session_send tool calls in one turn (fanout SKILL.md prose,
    # test_233_review_routing_activation.py's GateWordingConformanceTest
    # covers that prose separately) -- nothing here can execute an agent
    # turn. What IS code, and what a silent regression could actually flip,
    # is `build_plan`'s `suggested_plan.human_review` block: it is the one
    # machine-readable signal a crew lead's dispatch procedure and any
    # future automation would key off to decide "one wave, fire concurrently"
    # vs "these domains gate each other." Before this test, only the FAST
    # fixture (test_fast_is_canonical_exact_and_has_no_side_effects) checked
    # this field -- a regression that special-cased STANDARD or HIGH_RISK
    # into sequential waves (e.g. `wave: 2` for a later domain, or
    # `mode: "sequential"`) would have shipped undetected.
    def test_standard_and_high_risk_domains_share_one_parallel_wave(self):
        self._task(crew="crew_beta", size="M")
        standard = self._head({"deploy/lib/widget.txt": "ordinary change\n"})
        report, _ = self._report(standard)
        self.assertEqual(report["classification"]["profile"], "STANDARD")
        self.assertGreater(len(report["suggested_domains"]), 1)
        self.assertEqual(report["suggested_plan"]["human_review"], {
            "mode": "parallel", "wave": 1,
            "domains": report["suggested_domains"],
        })

        self._task(crew="crew_alpha", size="S", base=standard)
        high = self._head({"bin/nightshift-ctl": "ordinary\n"})
        report, _ = self._report(high)
        self.assertEqual(report["classification"]["profile"], "HIGH_RISK")
        self.assertGreater(len(report["suggested_domains"]), 1)
        self.assertEqual(report["suggested_plan"]["human_review"], {
            "mode": "parallel", "wave": 1,
            "domains": report["suggested_domains"],
        })
        # The two deterministic gates stay a strict two-wave sequence
        # (merge-result verification legitimately depends on the
        # deterministic gate having run) -- only `human_review` is required
        # to collapse independent domains into one wave. Confirming the
        # gates block is UNCHANGED here proves this test is not merely
        # asserting "everything is wave 1", which would be a vacuous
        # regression proof.
        self.assertEqual(report["suggested_plan"]["gates"], [
            {"gate": "deterministic", "wave": 1},
            {"gate": "exact_merge_result", "wave": 2, "after": [1]},
        ])

    # -- Issue #277 / ADR-0051, Required regression proof #15 (highest
    # priority of the four named gaps) -----------------------------------
    #
    # "Tests compare dispatch counts/model turns for representative
    # FAST/STANDARD/HIGH_RISK fixtures so future changes cannot silently
    # reintroduce universal maximum review." `suggested_domains` is the one
    # deterministic, code-computed proxy for "how many independent review
    # turns this change will cost" -- one crew-lead dispatch call per domain,
    # by fanout's own procedure (`sys_session_send` per domain plus one
    # deterministic-gate pass), so `dispatch_count = len(suggested_domains)`
    # is exactly the quantity a silent "route everything to every domain
    # always" regression would inflate. Five representative, INCREMENTAL
    # (single-commit-diff) fixtures chained from one base, spanning all
    # three profiles including a profile-boundary pair (two STANDARD
    # fixtures with different dispatch counts, proving the count tracks the
    # actual trigger rather than merely the profile label):
    #   FAST                         (docs/guide.md)            -> 2 domains
    #   STANDARD, qa-triggering      (deploy/lib/widget.txt)     -> 3 domains
    #   STANDARD, no extra trigger   (docs/ordinary.md)          -> 2 domains
    #   HIGH_RISK, workflow          (.github/workflows/*.yml)   -> 5 domains
    #   HIGH_RISK, control-plane     (bin/nightshift-ctl)        -> 6 domains
    # A regression that made every change dispatch every domain would
    # collapse all five counts to the same (maximal) number; a regression
    # that stopped narrowing at all for STANDARD would make STANDARD1 and
    # STANDARD2 equal. Both are caught here by construction, not by trusting
    # a single snapshot.
    def test_dispatch_counts_stay_narrow_and_distinct_across_risk_tiers(self):
        self._task(crew="program", size="S")
        fast = self._head({"docs/guide.md": "human guide\n"})
        fast_report, _ = self._report(fast)

        self._task(crew="crew_beta", size="M", base=fast)
        standard_qa = self._head({"deploy/lib/widget.txt": "ordinary change\n"})
        standard_qa_report, _ = self._report(standard_qa)

        self._task(crew="program", size="M", base=standard_qa)
        standard_plain = self._head({"docs/ordinary.md": "ordinary\n"})
        standard_plain_report, _ = self._report(standard_plain)

        self._task(crew="platform", size="S", base=standard_plain)
        high_workflow = self._head(
            {".github/workflows/check.yml": "name: check\n"})
        high_workflow_report, _ = self._report(high_workflow)

        self._task(crew="crew_alpha", size="S", base=high_workflow)
        high_control = self._head({"bin/nightshift-ctl": "ordinary\n"})
        high_control_report, _ = self._report(high_control)

        fixtures = [
            ("FAST", fast_report, 2, "FAST"),
            ("STANDARD_qa_trigger", standard_qa_report, 3, "STANDARD"),
            ("STANDARD_plain", standard_plain_report, 2, "STANDARD"),
            ("HIGH_RISK_workflow", high_workflow_report, 5, "HIGH_RISK"),
            ("HIGH_RISK_control_plane", high_control_report, 6, "HIGH_RISK"),
        ]
        dispatch_counts = {}
        for name, report, expected_count, expected_profile in fixtures:
            with self.subTest(fixture=name):
                self.assertEqual(report["classification"]["profile"],
                                 expected_profile)
                self.assertEqual(len(report["suggested_domains"]),
                                 expected_count,
                                 report["suggested_domains"])
                dispatch_counts[name] = len(report["suggested_domains"])

        # Model-turn estimate: one crew-lead turn to freeze the contract and
        # dispatch, plus one worker turn per dispatched review domain (the
        # deterministic gates cost no model turn at all). Recorded here as
        # an explicit formula, not just a count, so a future change to the
        # formula itself is a visible diff rather than a silent drift.
        def model_turns(report):
            return 1 + len(report["suggested_domains"])

        self.assertEqual(model_turns(fast_report), 3)
        self.assertEqual(model_turns(standard_qa_report), 4)
        self.assertEqual(model_turns(high_control_report), 7)

        # The actual anti-regression shape: never collapse to one number,
        # and never let a later, more heavily reviewed tier dispatch FEWER
        # domains than an earlier, lighter tier (fail-upward, proof #5,
        # restated here in dispatch-count terms rather than domain-set
        # terms).
        self.assertLess(dispatch_counts["FAST"],
                        dispatch_counts["STANDARD_qa_trigger"])
        self.assertLessEqual(dispatch_counts["FAST"],
                             dispatch_counts["STANDARD_plain"])
        self.assertLess(dispatch_counts["STANDARD_qa_trigger"],
                        dispatch_counts["HIGH_RISK_workflow"])
        self.assertLess(dispatch_counts["STANDARD_plain"],
                        dispatch_counts["HIGH_RISK_workflow"])
        self.assertLessEqual(dispatch_counts["HIGH_RISK_workflow"],
                             dispatch_counts["HIGH_RISK_control_plane"])
        # STANDARD's own two fixtures must differ from each other -- proves
        # the count tracks the real trigger (qa's conditional glob), not a
        # fixed per-profile constant a "universal max review" regression
        # could satisfy by accident.
        self.assertNotEqual(dispatch_counts["STANDARD_qa_trigger"],
                            dispatch_counts["STANDARD_plain"])


if __name__ == "__main__":
    unittest.main()
