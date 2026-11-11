"""Issue #400 -- Customer Publication Gateway, the provenance/footprint/
tenancy/dirty-checkout quarter (PG-4, PG-6, PG-7, PG-8) owned by
bin/nightshift_provenance.py. See ADR-0004, ADR-0005, ADR-0006.

Mockless throughout: every git fixture below is a REAL repository built
with real `git` subprocess calls in a tmpdir, every capture/compare call
runs REAL subprocess `git` commands (through the module's own
`customer_git()` choke point), and every provenance assertion is against a
REAL sqlite3 connection to a real provenance.db on disk. Nothing here is
mocked, stubbed or monkeypatched.

Every class below maps to one acceptance item this module owns:

  FootprintPositiveControls   -- PG-4: the harness actually catches the
                                  forbidden operations item 1 names (E5).
  FootprintStatusInsufficiency -- PG-4: git status alone is defeated three
                                  ways (E6); this module's harness is not.
  FootprintSelfProof          -- PG-4: capture() refuses its own evidence
                                  when its own commands perturb the repo.
  FootprintCleanRun           -- PG-4: a fresh run against a clean repo
                                  produces a clean verdict (item 1).
  CustomerGitGuard            -- PG-4: the sole exec choke point refuses
                                  every write-shaped subcommand and flag.
  MirrorAcquisitionIsNotClone -- PG-4/PG-8: E2/E3 -- fetch never hardlinks.
  TenancyPhysicalSeparation   -- PG-6: the six cross-tenant operations,
                                  each with its own negative test.
  ProvenanceLineage           -- PG-7: immutability, the one legal
                                  transition, chain closure, non-
                                  propagation, cycle/self-loop refusal.
  DirtyCheckoutSyntheticSnapshot -- PG-8: external copy, marked synthetic
                                  commit, determinism (E8), zero customer
                                  mutation end to end.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_provenance as prov  # noqa: E402 -- see sys.path insert above


def _git(repo, *args, check=True, env=None):
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


def make_repo(tmp_root, name="repo"):
    repo = os.path.join(tmp_root, name)
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tester@example.invalid")
    _git(repo, "config", "user.name", "tester")
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("hello\n")
    _git(repo, "add", "a.txt")
    fixed_env = dict(
        os.environ, GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
        GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
        GIT_AUTHOR_DATE="1600000000 +0000", GIT_COMMITTER_DATE="1600000000 +0000")
    _git(repo, "commit", "-q", "-m", "init", env=fixed_env)
    return repo


class FootprintCleanRun(unittest.TestCase):
    """#400 item 1: a fresh run against a clean customer repository
    produces no unrequested file, staging, ref, index, worktree-metadata,
    alternates, lock-file, ignore-file, or .git change. Checked by
    capturing BEFORE/AFTER a no-op (nothing at all happens in between) and
    by capturing before/after a genuinely read-only operation
    (repository_fingerprint), which must also leave the repo clean.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-prov-clean-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)

    def test_noop_between_captures_is_clean(self):
        before = prov.capture_footprint(self.repo, tier="B")
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])

    def test_repository_fingerprint_is_read_only(self):
        before = prov.capture_footprint(self.repo, tier="B")
        fp1 = prov.repository_fingerprint(self.repo)
        fp2 = prov.repository_fingerprint(self.repo)
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(fp1, fp2)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])

    def test_discover_closure_on_a_plain_repo_is_read_only_and_empty(self):
        before = prov.capture_footprint(self.repo, tier="B")
        entries = prov.discover_closure(self.repo)
        after = prov.capture_footprint(self.repo, tier="B")
        self.assertEqual(entries, [])
        self.assertEqual(prov.compare_footprint(before, after)["verdict"], "clean")


class ClosureDiscoverySubmoduleAndLfs(unittest.TestCase):
    """PG-7 / #400 item 11: provenance closure covers required submodule
    commits and Git LFS objects. Uses a REAL submodule (a second real repo,
    added with `git submodule add`) and a REAL LFS pointer blob (the exact
    ~130-byte pointer format, written without git-lfs installed -- the
    module's own claim is that pointer parsing needs no git-lfs binary).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-prov-closure-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sub = make_repo(self.tmp, name="sub")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "tester@example.invalid")
        _git(self.repo, "config", "user.name", "tester")
        _git(self.repo, "-c", "protocol.file.allow=always",
             "submodule", "add", self.sub, "vendor/sub")
        with open(os.path.join(self.repo, ".gitattributes"), "w") as fh:
            fh.write("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        self.lfs_oid_hex = "a" * 64
        pointer = (
            "version https://git-lfs.github.com/spec/v1\n"
            f"oid sha256:{self.lfs_oid_hex}\n"
            "size 12345\n")
        with open(os.path.join(self.repo, "big.bin"), "w") as fh:
            fh.write(pointer)
        _git(self.repo, "add", ".gitattributes", "big.bin")
        fixed_env = dict(
            os.environ, GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
            GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
            GIT_AUTHOR_DATE="1600000001 +0000", GIT_COMMITTER_DATE="1600000001 +0000")
        _git(self.repo, "commit", "-q", "-m", "vendor submodule + lfs pointer", env=fixed_env)

    def test_submodule_and_lfs_entries_discovered_read_only(self):
        before = prov.capture_footprint(self.repo, tier="B")
        entries = prov.discover_closure(self.repo)
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])

        by_kind = {(e["kind"], e["path"]): e for e in entries}
        self.assertIn(("submodule", "vendor/sub"), by_kind)
        self.assertIn(("lfs", "big.bin"), by_kind)
        self.assertEqual(by_kind[("lfs", "big.bin")]["digest"], "sha256:" + self.lfs_oid_hex)
        self.assertEqual(by_kind[("lfs", "big.bin")]["size"], 12345)

    def test_require_closure_complete_passes_once_both_are_recorded(self):
        entries = prov.discover_closure(self.repo)
        prov.require_closure_complete(["vendor/sub", "big.bin"], entries)  # must not raise

    def test_require_closure_complete_refuses_when_lfs_entry_is_missing(self):
        entries = [e for e in prov.discover_closure(self.repo) if e["kind"] != "lfs"]
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.require_closure_complete(["vendor/sub", "big.bin"], entries)
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(ctx.exception.details["missing"], ["big.bin"])


class FootprintPositiveControls(unittest.TestCase):
    """E5: the harness must actually FAIL on the operations item 1 names --
    a harness that never fails is not evidence of anything.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-prov-positive-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)

    def test_git_add_is_caught(self):
        before = prov.capture_footprint(self.repo, tier="B")
        with open(os.path.join(self.repo, "b.txt"), "w") as fh:
            fh.write("new\n")
        _git(self.repo, "add", "b.txt")
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "violation")
        self.assertTrue(any(v["manifest"] == "gitdir" for v in cmp["violations"]))

    def test_stash_push_is_caught(self):
        before = prov.capture_footprint(self.repo, tier="B")
        with open(os.path.join(self.repo, "c.txt"), "w") as fh:
            fh.write("untracked\n")
        _git(self.repo, "stash", "push", "-u", "-m", "x")
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "violation")

    def test_worktree_add_is_caught(self):
        before = prov.capture_footprint(self.repo, tier="A")
        wt_dir = os.path.join(self.tmp, "linked-wt")
        _git(self.repo, "worktree", "add", wt_dir, "-b", "feature")
        after = prov.capture_footprint(self.repo, tier="A")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "violation")
        self.assertTrue(any(v["manifest"] == "gitdir" for v in cmp["violations"]))


class FootprintStatusInsufficiency(unittest.TestCase):
    """E6: `git status` alone is defeated three independent ways. This
    module's harness must catch what a naive `git status --porcelain`
    check would miss.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-prov-e6-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)

    def test_e6a_ignore_file_edit_hidden_from_bare_status_caught_by_ignored_matching(self):
        os.makedirs(os.path.join(self.repo, ".omni"))
        with open(os.path.join(self.repo, ".omni", "state.json"), "w") as fh:
            fh.write("{}")
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a") as fh:
            fh.write(".omni/\n")
        bare = prov.customer_git(self.repo, ["status", "--porcelain=v1", "-z"])
        self.assertNotIn(".omni", bare.stdout,
                          "bare status without --ignored is expected to miss it")
        dirty = prov.detect_dirty(self.repo)
        found = any(".omni" in e["path"]
                    for e in dirty["tracked_or_untracked"] + dirty["ignored"])
        self.assertTrue(found, "detect_dirty's -uall --ignored=matching must catch it")

    def test_e6b_core_excludesfile_hidden_from_bare_uall_caught_by_ignored_matching(self):
        global_ignore = os.path.join(self.tmp, "global.gitignore")
        with open(global_ignore, "w") as fh:
            fh.write("SECRET.md\n")
        _git(self.repo, "config", "core.excludesFile", global_ignore)
        with open(os.path.join(self.repo, "SECRET.md"), "w") as fh:
            fh.write("leak\n")
        bare_uall = prov.customer_git(
            self.repo, ["status", "--porcelain=v1", "-z", "-uall"])
        self.assertNotIn("SECRET.md", bare_uall.stdout,
                          "-uall alone (no --ignored) is expected to miss a core.excludesFile hit")
        dirty = prov.detect_dirty(self.repo)
        found = any("SECRET.md" in e["path"] for e in dirty["ignored"])
        self.assertTrue(found)

    def test_e6c_showuntrackedfiles_no_makes_bare_ignored_probe_fail_not_silently_clean(self):
        _git(self.repo, "config", "status.showUntrackedFiles", "no")
        bare = subprocess.run(
            ["git", "status", "--porcelain", "--ignored=matching"],
            cwd=self.repo, capture_output=True, text=True)
        self.assertNotEqual(bare.returncode, 0,
                             "a bare --ignored invocation without -uall is expected to exit non-zero here")
        with open(os.path.join(self.repo, "LEAK.md"), "w") as fh:
            fh.write("leak\n")
        # our function ALWAYS passes -uall explicitly on argv, so it must
        # still work correctly despite the repo-level config:
        dirty = prov.detect_dirty(self.repo)
        found = any("LEAK.md" in e["path"] for e in dirty["tracked_or_untracked"])
        self.assertTrue(found)


class FootprintSelfProof(unittest.TestCase):
    """1.5: capture() must refuse its own evidence if its own commands
    perturbed the repository -- proven here by capturing twice in a row
    against a clean repo (must both succeed and agree) and separately by
    showing the self-proof fields are present.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-prov-selfproof-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)

    def test_capture_is_itself_non_mutating(self):
        cap1 = prov.capture_footprint(self.repo, tier="B")
        self.assertTrue(cap1["self_proof_clean"])
        cap2 = prov.capture_footprint(self.repo, tier="B")
        self.assertTrue(cap2["self_proof_clean"])
        self.assertEqual(
            prov._strip_mtime(cap1["gitdir_manifest"]),
            prov._strip_mtime(cap2["gitdir_manifest"]))

    @staticmethod
    def _index_fingerprint(index_path):
        with open(index_path, "rb") as fh:
            return (fh.read(), os.stat(index_path).st_mtime_ns)

    def test_plain_status_without_guard_would_touch_index_but_guarded_call_does_not(self):
        index_path = os.path.join(self.repo, ".git", "index")
        # E1: plain `git status --porcelain` (no --no-optional-locks) is a
        # known, expected trigger for a stale-stat-cache index rewrite, but
        # it is stat-cache dependent -- not asserted here to avoid a flaky
        # test; the guarded call below is what this test actually proves.
        subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, capture_output=True)

        before2 = self._index_fingerprint(index_path)
        prov.customer_git(self.repo, ["status", "--porcelain=v1", "-z"])
        after2 = self._index_fingerprint(index_path)
        self.assertEqual(before2, after2,
                          "customer_git's --no-optional-locks must leave the index untouched")


class CustomerGitGuard(unittest.TestCase):
    """PG-4 1.8: the sole exec choke point refuses every write-shaped
    subcommand and every flag that could smuggle one past a subcommand-
    name check.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-prov-guard-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)

    def _assert_refused(self, args):
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.customer_git(self.repo, args)
        self.assertEqual(ctx.exception.reason_code, "customer_repo_write_refused")

    def test_refuses_write_subcommands(self):
        for args in (["add", "-A"], ["commit", "-m", "x"], ["update-ref", "refs/x", "HEAD"],
                     ["notes", "add", "-m", "x"], ["gc"], ["repack", "-a"]):
            with self.subTest(args=args):
                self._assert_refused(args)

    def test_refuses_multi_word_write_variants(self):
        for args in (["worktree", "add", "/tmp/x"], ["stash", "push"],
                     ["submodule", "update"], ["config", "--add", "x.y", "z"],
                     ["config", "--unset", "x.y"]):
            with self.subTest(args=args):
                self._assert_refused(args)

    def test_refuses_smuggled_config_and_dir_overrides(self):
        for args in (["status", "-c", "core.hooksPath=/tmp/evil"],
                     ["status", "--git-dir=/tmp/elsewhere"],
                     ["status", "--work-tree=/tmp/elsewhere"],
                     ["status", "-C", "/tmp/elsewhere"]):
            with self.subTest(args=args):
                self._assert_refused(args)

    def test_allows_the_read_only_surface(self):
        for args in (["status", "--porcelain=v1", "-z", "-uall", "--ignored=matching"],
                     ["rev-parse", "HEAD"], ["for-each-ref"], ["ls-files", "-z"],
                     ["worktree", "list", "--porcelain"], ["stash", "list"],
                     ["config", "--list"], ["submodule", "status"]):
            with self.subTest(args=args):
                proc = prov.customer_git(self.repo, args)
                self.assertEqual(proc.returncode, 0, proc.stderr)


class MirrorAcquisitionIsNotClone(unittest.TestCase):
    """E2/E3: acquire_mirror() must never hardlink customer loose objects
    the way `git clone --bare` does.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-prov-mirror-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)

    def _loose_objects(self):
        obj_dir = os.path.join(self.repo, ".git", "objects")
        out = []
        for d in os.listdir(obj_dir):
            if len(d) == 2:
                for f in os.listdir(os.path.join(obj_dir, d)):
                    out.append(os.path.join(obj_dir, d, f))
        return out

    def test_clone_bare_hardlinks_but_acquire_mirror_does_not(self):
        loose = self._loose_objects()
        self.assertTrue(loose)
        before = {f: os.stat(f).st_nlink for f in loose}

        clone_target = os.path.join(self.tmp, "clone-bare")
        subprocess.run(["git", "clone", "--bare", self.repo, clone_target],
                        capture_output=True)
        after_clone = {f: os.stat(f).st_nlink for f in loose}
        self.assertNotEqual(before, after_clone,
                             "positive control: bare clone is expected to hardlink")

        before2 = {f: os.stat(f).st_nlink for f in loose}
        mirror = os.path.join(self.tmp, "mirror-fetch")
        prov.acquire_mirror(self.repo, mirror)
        after2 = {f: os.stat(f).st_nlink for f in loose}
        self.assertEqual(before2, after2,
                          "acquire_mirror (fetch) must never change customer st_nlink")

    def test_acquire_mirror_wrapped_in_footprint_proof_is_clean(self):
        before = prov.capture_footprint(self.repo, tier="B")
        mirror = os.path.join(self.tmp, "mirror2")
        prov.acquire_mirror(self.repo, mirror)
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])


class TenancyPhysicalSeparation(unittest.TestCase):
    """PG-6 / #400 item 9: cross-tenant reads, searches, dedup, exports,
    and deletions all fail closed. (Cross-tenant *approval* consumption is
    PG-2's table, owned by bin/nightshift_publication.py -- not built
    here; the tenant-in-the-WHERE-clause CAS pattern this module
    establishes in mark_source_not_retained() is what that module reuses.)
    """

    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="ns-prov-tenancy-")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.cx_a = prov.connect(self.state_root, 1, 100)
        self.addCleanup(self.cx_a.close)
        self.cx_b = prov.connect(self.state_root, 2, 100)
        self.addCleanup(self.cx_b.close)
        self.fields = dict(
            repository_fingerprint="fp", object_format="sha1",
            commit_oid="c" * 40, tree_oid="t" * 40, captured_at=1.0)
        prov.record_source_snapshot(self.cx_a, 1, 100, "snapA", self.fields)

    def test_physical_directories_differ(self):
        root_a = prov.resolve_publication_root(self.state_root, 1, 100)
        root_b = prov.resolve_publication_root(self.state_root, 2, 100)
        self.assertNotEqual(root_a, root_b)
        self.assertTrue(os.path.isdir(root_a))
        self.assertEqual(oct(os.stat(root_a).st_mode & 0o777), "0o700")

    def test_invalid_scope_ids_refused_before_path_join(self):
        for bad in (True, False, -1, 0, "1", None, 1.5):
            with self.subTest(bad=bad):
                with self.assertRaises(prov.ProvenanceError) as ctx:
                    prov.resolve_publication_root(self.state_root, bad, 100)
                self.assertEqual(ctx.exception.reason_code, "invalid_tenant_id")

    def test_cross_tenant_read_indistinguishable_from_not_found(self):
        cross = prov.get_source_snapshot(self.cx_b, 2, 100, "snapA")
        missing = prov.get_source_snapshot(self.cx_b, 2, 100, "does-not-exist")
        self.assertIsNone(cross)
        self.assertIsNone(missing)
        self.assertEqual(cross, missing)

    def test_cross_tenant_search_never_returns_or_counts_the_row(self):
        listing = prov.list_source_snapshots(self.cx_b, 2, 100)
        self.assertEqual(listing, [])

    def test_cross_tenant_dedupe_two_independent_locators_no_oracle(self):
        data = b"identical bytes staged by two tenants"
        digest = hashlib.sha256(data).hexdigest()
        path_a = prov.stage_object(self.state_root, 1, 100, digest, data)
        self.assertFalse(prov.object_exists(self.state_root, 2, 100, digest),
                          "tenant B must not be able to observe tenant A's staged object")
        path_b = prov.stage_object(self.state_root, 2, 100, digest, data)
        self.assertNotEqual(path_a, path_b)
        self.assertTrue(os.path.exists(path_a))
        self.assertTrue(os.path.exists(path_b))

    def test_cross_tenant_export_contains_zero_rows(self):
        export = prov.export_source_snapshots(self.cx_b, 2, 100)
        self.assertEqual(export, [])

    def test_cross_tenant_delete_of_staged_object_touches_nothing(self):
        data = b"tenant A's private bytes"
        digest = hashlib.sha256(data).hexdigest()
        path_a = prov.stage_object(self.state_root, 1, 100, digest, data)
        with open(path_a, "rb") as fh:
            before = fh.read()
        deleted = prov.purge_staged_object(self.state_root, 2, 100, digest)
        self.assertFalse(deleted, "tenant B has no object at that digest to delete")
        self.assertTrue(os.path.exists(path_a))
        with open(path_a, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_cross_tenant_lineage_purge_deletes_nothing(self):
        prov.record_lineage_node(self.cx_a, 1, 100, "nodeA", "source_snapshot", "s", "snapA")
        victims = prov.purge_lineage_node(self.cx_b, 2, 100, "nodeA")
        self.assertEqual(victims, set())
        row = self.cx_a.execute(
            "SELECT * FROM lineage_node WHERE node_id='nodeA'").fetchone()
        self.assertIsNotNone(row)

    def test_cross_tenant_derived_from_edge_refused(self):
        prov.record_lineage_node(self.cx_a, 1, 100, "nA", "source_snapshot", "s", "snapA")
        prov.record_lineage_node(self.cx_b, 2, 100, "nB", "worker_output", "s", "x")
        with self.assertRaises(prov.ProvenanceError):
            prov.record_derived_from(self.cx_a, 1, 100, "nA", "nB", "derives", 0)

    def test_defense_in_depth_same_file_cross_tenant_edge_refused(self):
        """Even if physical separation were somehow bypassed (a row from
        another tenant ending up in this file), the query-level tenant
        filter independently refuses the edge.
        """
        prov.record_lineage_node(self.cx_a, 1, 100, "own", "worker_output", "s", "x")
        self.cx_a.execute(
            "INSERT INTO lineage_node(node_id,tenant_id,repository_id,node_kind,ref_kind,ref_id,created_at) "
            "VALUES('leaked',2,100,'worker_output','s','y',1.0)")
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_derived_from(self.cx_a, 1, 100, "own", "leaked", "derives", 0)
        self.assertEqual(ctx.exception.reason_code, "lineage_cross_tenant_refused")


class ProvenanceLineage(unittest.TestCase):
    """PG-7: immutability, the one legal transition, chain closure,
    non-propagation, cycle/self-loop refusal.
    """

    def setUp(self):
        self.state_root = tempfile.mkdtemp(prefix="ns-prov-lineage-")
        self.addCleanup(shutil.rmtree, self.state_root, ignore_errors=True)
        self.cx = prov.connect(self.state_root, 1, 100)
        self.addCleanup(self.cx.close)
        self.fields = dict(
            repository_fingerprint="fp", object_format="sha1",
            commit_oid="c" * 40, tree_oid="t" * 40, captured_at=1.0)
        prov.record_source_snapshot(self.cx, 1, 100, "snap1", self.fields)

    def test_arbitrary_update_refused_by_trigger(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.cx.execute("UPDATE source_snapshot SET commit_oid='x' WHERE snapshot_id='snap1'")

    def test_legal_transition_succeeds_and_is_one_use(self):
        prov.mark_source_not_retained(self.cx, 1, 100, "snap1", "ttl_expired")
        row = prov.get_source_snapshot(self.cx, 1, 100, "snap1")
        self.assertEqual(row["source_not_retained"], 1)
        self.assertEqual(row["retention_state"], "expired")
        with self.assertRaises(prov.ProvenanceError):
            prov.mark_source_not_retained(self.cx, 1, 100, "snap1", "again")

    def test_other_immutable_columns_survive_the_transition_unchanged(self):
        before = prov.get_source_snapshot(self.cx, 1, 100, "snap1")
        prov.mark_source_not_retained(self.cx, 1, 100, "snap1", "reason")
        after = prov.get_source_snapshot(self.cx, 1, 100, "snap1")
        for col in ("commit_oid", "tree_oid", "object_format", "repository_fingerprint"):
            self.assertEqual(before[col], after[col])

    def _wire_chain(self):
        prov.record_lineage_node(self.cx, 1, 100, "n_snap", "source_snapshot", "s", "snap1")
        prov.record_lineage_node(self.cx, 1, 100, "n_worker", "worker_output", "a", "art1")
        prov.record_lineage_node(self.cx, 1, 100, "n_patch", "reviewed_patch", "ap", "appr1")
        prov.record_lineage_node(self.cx, 1, 100, "n_result", "published_result", "r", "rcpt1")
        prov.record_derived_from(self.cx, 1, 100, "n_snap", "n_worker", "derives", 0)
        prov.record_derived_from(self.cx, 1, 100, "n_worker", "n_patch", "reviews", 1)
        prov.record_derived_from(self.cx, 1, 100, "n_patch", "n_result", "publishes", 1)

    def test_chain_closes_when_all_links_present(self):
        self._wire_chain()
        chain = prov.lineage_chain(self.cx, 1, 100, "n_result")
        self.assertTrue(chain["closed"])
        self.assertEqual(chain["chain"], ["n_snap", "n_worker", "n_patch", "n_result"])

    def test_chain_incomplete_when_a_link_is_missing(self):
        prov.record_lineage_node(self.cx, 1, 100, "n_result_only", "published_result", "r", "rcpt2")
        chain = prov.lineage_chain(self.cx, 1, 100, "n_result_only")
        self.assertFalse(chain["closed"])
        self.assertEqual(chain["reason"], "lineage_incomplete")

    def test_cycle_refused(self):
        self._wire_chain()
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_derived_from(self.cx, 1, 100, "n_result", "n_snap", "derives", 0)
        self.assertEqual(ctx.exception.reason_code, "lineage_cycle_refused")

    def test_self_loop_refused(self):
        prov.record_lineage_node(self.cx, 1, 100, "solo", "worker_output", "a", "b")
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.record_derived_from(self.cx, 1, 100, "solo", "solo", "derives", 1)
        self.assertEqual(ctx.exception.reason_code, "lineage_self_loop_refused")

    def test_source_not_retained_does_not_propagate_to_derived_narrative(self):
        """ADR-0005, verbatim: 'expiry of a Git source snapshot is
        non-propagating'. The `derives` edge from the snapshot carries
        content_carrying=0, so a content-carrying walk starting at the
        snapshot stops immediately, and marking the snapshot expired never
        even attempts to touch the worker_output/patch/result chain.
        """
        self._wire_chain()
        prov.mark_source_not_retained(self.cx, 1, 100, "snap1", "expired")
        descendants = prov.content_carrying_descendants(self.cx, 1, 100, "n_snap")
        self.assertEqual(descendants, {"n_snap"})
        for node_id in ("n_worker", "n_patch", "n_result"):
            row = self.cx.execute(
                "SELECT * FROM lineage_node WHERE node_id=?", (node_id,)).fetchone()
            self.assertIsNotNone(row, f"{node_id} must survive source expiry untouched")

    def test_redaction_walk_from_worker_output_reaches_its_content_carrying_descendants(self):
        self._wire_chain()
        descendants = prov.content_carrying_descendants(self.cx, 1, 100, "n_worker")
        self.assertEqual(descendants, {"n_worker", "n_patch", "n_result"})

    def test_closure_entries_recorded_and_required_paths_checked(self):
        prov.record_closure_entry(self.cx, 1, 100, "snap1", "submodule", "vendor/x", "d" * 40)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.require_closure_complete(["vendor/x", "vendor/y"],
                                           [{"path": "vendor/x"}])
        self.assertEqual(ctx.exception.reason_code, "closure_incomplete")
        self.assertEqual(ctx.exception.details["missing"], ["vendor/y"])


class DirtyCheckoutSyntheticSnapshot(unittest.TestCase):
    """PG-8: external copy, marked synthetic commit, determinism (E8),
    zero customer mutation end to end.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-prov-dirty-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp)
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("hello modified\n")
        with open(os.path.join(self.repo, "new.txt"), "w") as fh:
            fh.write("new untracked\n")
        self.mirror = os.path.join(self.tmp, "mirror")
        prov.acquire_mirror(self.repo, self.mirror)
        self.ws = os.path.join(self.tmp, "ws")
        prov.create_workspace(self.mirror, self.ws)

    def test_policy_default_refuse_blocks_before_any_snapshot_work(self):
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.require_dirty_policy_allows("refuse")
        self.assertEqual(ctx.exception.reason_code, "dirty_checkout_refused")

    def test_capture_dirty_snapshot_leaves_customer_repo_untouched(self):
        before = prov.capture_footprint(self.repo, tier="B")
        result = prov.capture_dirty_snapshot(
            self.repo, self.mirror, self.ws,
            snapshot_id="snap1", capture_time="1700000000 +0000")
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])
        self.assertEqual(result["synthetic"], 1)
        self.assertEqual(result["dirty_capture_method"], "external_copy")
        self.assertTrue(os.path.isfile(result["bundle_path"]))

    def test_synthetic_snapshot_is_marked_not_presented_as_upstream(self):
        result = prov.capture_dirty_snapshot(
            self.repo, self.mirror, self.ws,
            snapshot_id="snap2", capture_time="1700000000 +0000")
        cx = prov.connect(self.tmp, 1, 100)
        self.addCleanup(cx.close)
        fields = dict(
            repository_fingerprint=prov.repository_fingerprint(self.repo),
            object_format="sha1",
            commit_oid=result["commit_oid"], tree_oid=result["tree_oid"],
            captured_at=1.0, synthetic=result["synthetic"],
            synthetic_parent_commit_oid=result["synthetic_parent_commit_oid"],
            patch_digest=result["patch_digest"],
            dirty_capture_method=result["dirty_capture_method"],
            worktree_file_manifest_digest=result["worktree_file_manifest_digest"],
            included_ignored=result["included_ignored"],
            retention_ref=result["retention_ref"], pack_digest=result["pack_digest"])
        prov.record_source_snapshot(cx, 1, 100, "snap2", fields)
        row = prov.get_source_snapshot(cx, 1, 100, "snap2")
        self.assertEqual(row["synthetic"], 1)
        self.assertEqual(row["synthetic_parent_commit_oid"], result["synthetic_parent_commit_oid"])

    def test_determinism_same_base_and_dirty_bytes_same_oid_and_patch_digest(self):
        """E8: fixed identity/dates -> reproducible commit OID and patch
        digest across two INDEPENDENT fixtures (separate tmpdirs, separate
        mirrors/workspaces), not merely the same process re-running.
        """
        def run_once(tag):
            tmp = tempfile.mkdtemp(prefix=f"ns-prov-e8-{tag}-")
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            repo = make_repo(tmp)
            with open(os.path.join(repo, "a.txt"), "w") as fh:
                fh.write("hello modified\n")
            with open(os.path.join(repo, "new.txt"), "w") as fh:
                fh.write("new untracked\n")
            mirror = os.path.join(tmp, "mirror")
            prov.acquire_mirror(repo, mirror)
            ws = os.path.join(tmp, "ws")
            prov.create_workspace(mirror, ws)
            return prov.capture_dirty_snapshot(
                repo, mirror, ws, snapshot_id="snapX",
                capture_time="1700000000 +0000")

        r1 = run_once("a")
        r2 = run_once("b")
        self.assertEqual(r1["commit_oid"], r2["commit_oid"])
        self.assertEqual(r1["tree_oid"], r2["tree_oid"])
        self.assertEqual(r1["patch_digest"], r2["patch_digest"])
        self.assertEqual(r1["worktree_file_manifest_digest"], r2["worktree_file_manifest_digest"])

    def test_clean_repo_refuses_not_dirty(self):
        clean_tmp = tempfile.mkdtemp(prefix="ns-prov-notdirty-")
        self.addCleanup(shutil.rmtree, clean_tmp, ignore_errors=True)
        clean_repo = make_repo(clean_tmp)
        mirror = os.path.join(clean_tmp, "mirror")
        prov.acquire_mirror(clean_repo, mirror)
        ws = os.path.join(clean_tmp, "ws")
        prov.create_workspace(mirror, ws)
        with self.assertRaises(prov.ProvenanceError) as ctx:
            prov.capture_dirty_snapshot(
                clean_repo, mirror, ws, snapshot_id="s",
                capture_time="1700000000 +0000")
        self.assertEqual(ctx.exception.reason_code, "not_dirty")


if __name__ == "__main__":
    unittest.main()
