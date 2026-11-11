"""Issue #400 -- Customer Publication Gateway, PG-4 (zero-footprint proof)
applied specifically to the publication pipeline in bin/nightshift_publication.py.

Item 1 is literal: "A fresh run against a clean customer repo produces NO
unrequested file, staging, ref, index, worktree-metadata, alternates,
lock-file, ignore-file, or .git change." A `git status --porcelain` check
alone is NOT sufficient -- the architect's design doc for this module
measured that `hash-object -w` mutates `.git/objects` while leaving `git
status` clean and `.git/index` byte-identical. So every assertion here
captures FOUR independent things before and after, exactly as specified:
`git status --porcelain`, `git rev-parse HEAD`, the full ref list
(`git for-each-ref` -- the local equivalent of the wire-protocol `ls-refs`
command against a repo with no remote helper in play), and a recursive,
content-hashed listing of `.git/` (or, for a bare repository, of the git-dir
itself, which for a bare repository IS the whole repository).

Every fixture below is a REAL git repository built with real `git`
subprocess calls; every gateway operation goes through the REAL
bin/nightshift_publication.py pipeline (policy-set -> reviewer-add -> stage
-> review -> approve -> apply/patch) against a REAL SQLite publication.db
on disk. Nothing here is mocked, stubbed or monkeypatched.

Where a corroborating second, independently-implemented proof is cheap
(the fixture cases, and the read-only testbed A testbed case), this file
ALSO brackets the same operations with bin/nightshift_provenance.py's
`capture_footprint`/`compare_footprint` (already verified against this
exact testbed by a sibling task) -- two independently-written footprint
mechanisms that must BOTH report clean catches a bug either one alone
might miss.

Classes:
  ZeroFootprintFreshRun            -- the core item-1 proof, and that a
                                       REFUSED write leaves zero footprint
                                       too (strict mode; non-bare target).
  ApplyFootprintIsTheAuthorizedDiff -- positive control: an ACTUALLY
                                       authorized publish changes ONLY what
                                       the authorization named, nothing else
                                       (distinguishes "requested" writes,
                                       which item 1 permits, from
                                       "unrequested" ones, which it forbids).
  BorrowedTestbedReadOnly        -- the same item-1 proof run against the
                                       real, large, sealed testbed A
                                       testbed, read-only (no apply).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from publication_identities import producer_token  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))

import nightshift_publication as pub  # noqa: E402 -- see sys.path insert above
import nightshift_provenance as prov  # noqa: E402

BORROWED_TESTBED = "/home/operator/testbeds/.nightshift-testbed/testbed A"

T0 = 1_700_000_000.0

# #400 item 10, structurally required by stage() itself since the PG-7 fix:
# every source_snapshot must carry these four identity fields. Fixed,
# well-formed placeholder values -- this file's fixtures are not exercising
# PG-7 identity/closure semantics themselves (tests/test_provenance_lineage.py
# does, against real git fixtures), only the zero/no-generated-file footprint
# guarantees, so a real repository_fingerprint is unnecessary here.
FIXTURE_SNAPSHOT_IDENTITY = {
    "repository_fingerprint": "sha256:" + "a" * 64,
    "object_format": "sha1",
    "commit_oid": "b" * 40,
    "tree_oid": "c" * 40,
}
# Since #694 stage() names a REAL source_snapshot row in the sibling
# provenance.db instead of taking a dict; GatewayHarness records one.
FIXTURE_SNAPSHOT_ID = "snap-footprint-fixture"


# ---------------------------------------------------------------------------
# Fixture builders -- real git subprocess only.
# ---------------------------------------------------------------------------

def _run_git_dir(git_dir, work_tree, *args, check=True):
    cmd = ["git", "--git-dir", git_dir]
    if work_tree is not None:
        cmd += ["--work-tree", work_tree]
    cmd += list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


_FIXED_COMMIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="tester", GIT_AUTHOR_EMAIL="tester@example.invalid",
    GIT_COMMITTER_NAME="tester", GIT_COMMITTER_EMAIL="tester@example.invalid",
    GIT_AUTHOR_DATE="1600000000 +0000", GIT_COMMITTER_DATE="1600000000 +0000",
)


def make_worktree_repo(tmp_root, name="repo"):
    """A real, NON-bare checkout -- the shape a customer's own working
    repository actually has (a work tree, a `.git` directory, no bareness).
    """
    repo = os.path.join(tmp_root, name)
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "tester@example.invalid"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "tester"], check=True)
    os.makedirs(os.path.join(repo, "docs"))
    with open(os.path.join(repo, "docs", "README.md"), "w") as fh:
        fh.write("existing doc\n")
    subprocess.run(["git", "-C", repo, "add", "docs/README.md"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"],
                    env=_FIXED_COMMIT_ENV, check=True)
    return repo


def make_bare_target(tmp_root, name="target.git"):
    """A bare publication target, seeded with one commit, built by cloning
    a throwaway seed checkout `--bare` -- never by hand-assembling objects,
    so the fixture itself is unimpeachably a real git history."""
    seed = make_worktree_repo(tmp_root, name="seed")
    bare = os.path.join(tmp_root, name)
    subprocess.run(["git", "clone", "-q", "--bare", seed, bare], check=True)
    shutil.rmtree(seed)
    return bare


# ---------------------------------------------------------------------------
# The footprint snapshot -- exactly the four probes #400 item 1 names.
# ---------------------------------------------------------------------------

def _recursive_git_dir_listing(git_dir):
    """Full recursive listing of the git-dir: relative path, POSIX mode,
    size, and (for regular files) a sha256 content hash. mtime is
    deliberately excluded -- an mtime-only touch is not a footprint
    violation in this module's own design (see nightshift_provenance's
    `_benign_touch`); every OTHER field is compared exactly."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(git_dir):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, git_dir)
            st = os.lstat(full)
            entry = {"path": rel, "mode": oct(stat.S_IMODE(st.st_mode))}
            if stat.S_ISLNK(st.st_mode):
                entry["kind"] = "symlink"
                entry["target"] = os.readlink(full)
            elif stat.S_ISREG(st.st_mode):
                entry["kind"] = "file"
                entry["size"] = st.st_size
                with open(full, "rb") as fh:
                    entry["sha256"] = hashlib.sha256(fh.read()).hexdigest()
            else:
                entry["kind"] = "other"
            entries.append(entry)
    return entries


def snapshot(*, git_dir, work_tree=None):
    """The four probes, verbatim: `git status --porcelain` (only meaningful
    with a work tree -- a bare repo has none, and is recorded as such
    rather than skipped silently), `git rev-parse HEAD`, the full ref list
    (`for-each-ref`, this repo's local equivalent of `ls-refs`), and a
    recursive listing of the git-dir."""
    status = (_run_git_dir(git_dir, work_tree, "status", "--porcelain").stdout
              if work_tree is not None else None)
    head_result = _run_git_dir(git_dir, work_tree, "rev-parse", "--verify", "-q", "HEAD", check=False)
    head = head_result.stdout.strip() if head_result.returncode == 0 else None
    refs = _run_git_dir(git_dir, work_tree, "for-each-ref").stdout
    listing = _recursive_git_dir_listing(git_dir)
    return {"status": status, "head": head, "refs": refs, "listing": listing}


def assert_git_dir_unchanged(test, before, after, msg=""):
    test.assertEqual(before["status"], after["status"], f"{msg}: git status --porcelain changed")
    test.assertEqual(before["head"], after["head"], f"{msg}: rev-parse HEAD changed")
    test.assertEqual(before["refs"], after["refs"], f"{msg}: ref list (for-each-ref / ls-refs) changed")
    test.assertEqual(before["listing"], after["listing"], f"{msg}: recursive .git/ listing changed")


def _diff_listing(before, after):
    by_path_before = {e["path"]: e for e in before}
    by_path_after = {e["path"]: e for e in after}
    added = sorted(set(by_path_after) - set(by_path_before))
    removed = sorted(set(by_path_before) - set(by_path_after))
    changed = sorted(
        p for p in (set(by_path_before) & set(by_path_after))
        if by_path_before[p] != by_path_after[p]
    )
    return added, removed, changed


# ---------------------------------------------------------------------------
# Gateway pipeline helper -- runs the module directly (no subprocess CLI),
# the same way stage()/review()/approve()/apply_publication() are called
# from bin/nightshift_publication.py's own cmd_* wrappers.
# ---------------------------------------------------------------------------

class GatewayHarness:
    """One tenant/repository's publication.db plus a registered reviewer,
    built the same way the CLI wrappers build it: `resolve_publication_root`
    then `connect`."""

    def __init__(self, state_root, *, tenant_id=1001, repository_id=1):
        self.tenant_id = tenant_id
        self.repository_id = repository_id
        # #694: provenance.db first -- publication.db is now its sibling in
        # the same tenant-scoped directory and ATTACHes it read-only, and
        # stage() will not accept a snapshot that is not a row in it.
        self.prov_cx = prov.connect(state_root, tenant_id, repository_id)
        self.snapshot_id = FIXTURE_SNAPSHOT_ID
        if prov.get_source_snapshot(self.prov_cx, tenant_id, repository_id,
                                     self.snapshot_id) is None:
            prov.record_source_snapshot(
                self.prov_cx, tenant_id, repository_id, self.snapshot_id,
                dict(FIXTURE_SNAPSHOT_IDENTITY, captured_at=T0))
        self.root = pub.resolve_publication_root(state_root, tenant_id, repository_id)
        self.cx = pub.connect(self.root)
        self.state_root = self.root

    def close(self):
        self.cx.close()
        self.prov_cx.close()

    def set_policy(self, *, mode, target_git_dir, target_ref, path_allowlist, t=T0):
        return pub.policy_set(
            self.cx, tenant_id=self.tenant_id, repository_id=self.repository_id,
            mode=mode, path_allowlist=path_allowlist, target_git_dir=target_git_dir,
            target_ref=target_ref, key_version=None, t=t,
        )

    def add_reviewer(self, identity="reviewer_bob", token=b"reviewer-token-1", t=T0):
        return pub.register_reviewer(
            self.cx, tenant_id=self.tenant_id, reviewer_identity=identity,
            token_bytes=token, t=t,
        )

    def stage(self, *, content=b"hello doc\n", path="docs/PROBE.md",
              producer="worker_alice", run_id="run-1", classification="design_doc", t=T0):
        return pub.stage(
            self.cx, tenant_id=self.tenant_id, repository_id=self.repository_id,
            content_bytes=content, destination_path=path, classification=classification,
            producer_identity=producer,
            producer_token_bytes=producer_token(pub, self.cx, self.tenant_id, producer), producer_run_id=run_id,
            snapshot_id=self.snapshot_id, t=t,
        )

    def review(self, *, stage_id, reviewer="reviewer_bob", token=b"reviewer-token-1",
               run_id="review-run-1", verdict="approve", t=T0):
        return pub.review(
            self.cx, tenant_id=self.tenant_id, repository_id=self.repository_id,
            stage_id=stage_id, reviewer_identity=reviewer, reviewer_run_id=run_id,
            token_bytes=token, verdict=verdict, t=t,
        )

    def approve(self, *, stage_id, review_id, expected_base_sha, ttl=60,
                reviewer="reviewer_bob", token=b"reviewer-token-1", run_id="review-run-1", t=T0):
        return pub.approve(
            self.cx, tenant_id=self.tenant_id, repository_id=self.repository_id,
            stage_id=stage_id, review_id=review_id, reviewer_identity=reviewer,
            reviewer_run_id=run_id, token_bytes=token,
            expected_base_sha=expected_base_sha, ttl=ttl, t=t,
        )

    def apply(self, *, authorization_id, stage_id, idempotency_key, t=T0):
        return pub.apply_publication(
            self.cx, tenant_id=self.tenant_id, repository_id=self.repository_id,
            authorization_id=authorization_id, stage_id=stage_id,
            idempotency_key=idempotency_key, state_root=self.state_root, t=t,
        )

    def patch(self, *, stage_id):
        return pub.patch_stage(self.cx, stage_id=stage_id, tenant_id=self.tenant_id,
                                repository_id=self.repository_id)


# ---------------------------------------------------------------------------
class ZeroFootprintFreshRun(unittest.TestCase):
    """#400 item 1, core proof: the customer's own checkout is where
    Nightshift observes state from (approve() reads `expected_base_sha`
    and the base tree via read-only `git` calls against `target_git_dir`).
    A full stage -> review -> approve -> patch run, with nothing ever
    APPROVED to write into THIS repo via `apply`, must leave it byte-for-
    byte identical -- .git included."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-pubfoot-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_worktree_repo(self.tmp)
        self.git_dir = os.path.join(self.repo, ".git")
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)

    def test_stage_review_approve_patch_leaves_repo_untouched(self):
        before = snapshot(git_dir=self.git_dir, work_tree=self.repo)
        before_prov = prov.capture_footprint(self.repo, tier="B")

        head = _run_git_dir(self.git_dir, self.repo, "rev-parse", "HEAD").stdout.strip()
        gw = GatewayHarness(self.state_root)
        gw.set_policy(mode="zero_nightshift_file", target_git_dir=self.git_dir,
                      target_ref="refs/heads/main", path_allowlist=["docs/**"])
        gw.add_reviewer()
        st = gw.stage()
        self.assertEqual(st["status"], "staged")
        rv = gw.review(stage_id=st["stage_id"])
        self.assertEqual(rv["verdict"], "approve")
        ap = gw.approve(stage_id=st["stage_id"], review_id=rv["review_id"],
                        expected_base_sha=head)
        self.assertTrue(ap["ok"])
        patch = gw.patch(stage_id=st["stage_id"])
        self.assertIn("PROBE.md", patch["patch"])
        gw.close()

        after = snapshot(git_dir=self.git_dir, work_tree=self.repo)
        after_prov = prov.capture_footprint(self.repo, tier="B")
        assert_git_dir_unchanged(self, before, after, "stage/review/approve/patch")
        cmp = prov.compare_footprint(before_prov, after_prov)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])

    def test_refused_write_leaves_repo_untouched(self):
        """A NON-bare target is a customer's live checkout, never a
        publication destination -- `apply` must refuse it
        (`target_is_source_checkout`) BEFORE any git mutation. The refusal
        itself must be footprint-clean, not merely fail with the right
        error code."""
        before = snapshot(git_dir=self.git_dir, work_tree=self.repo)

        head = _run_git_dir(self.git_dir, self.repo, "rev-parse", "HEAD").stdout.strip()
        gw = GatewayHarness(self.state_root)
        gw.set_policy(mode="zero_nightshift_file", target_git_dir=self.git_dir,
                      target_ref="refs/heads/main", path_allowlist=["docs/**"])
        gw.add_reviewer()
        st = gw.stage()
        rv = gw.review(stage_id=st["stage_id"])
        ap = gw.approve(stage_id=st["stage_id"], review_id=rv["review_id"],
                        expected_base_sha=head)
        with self.assertRaises(pub.PublicationError) as ctx:
            gw.apply(authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
                     idempotency_key="probe-1")
        self.assertEqual(ctx.exception.reason_code, "target_is_source_checkout")
        gw.close()

        after = snapshot(git_dir=self.git_dir, work_tree=self.repo)
        assert_git_dir_unchanged(self, before, after, "refused apply (non-bare target)")

    def test_strict_mode_refuses_before_any_write_and_leaves_repo_clean(self):
        """PG-5: `no_generated_file` mode disables publication entirely.
        `approve` must refuse before an authorization row -- let alone a
        git write -- ever exists, and the repo must stay untouched."""
        before = snapshot(git_dir=self.git_dir, work_tree=self.repo)

        gw = GatewayHarness(self.state_root)
        gw.set_policy(mode="no_generated_file", target_git_dir=None, target_ref=None,
                      path_allowlist=["docs/**"])
        gw.add_reviewer()
        st = gw.stage()
        rv = gw.review(stage_id=st["stage_id"])
        with self.assertRaises(pub.PublicationError) as ctx:
            gw.approve(stage_id=st["stage_id"], review_id=rv["review_id"],
                       expected_base_sha="0" * 40)
        self.assertEqual(ctx.exception.reason_code, "publication_disabled_no_generated_file")
        patch = gw.patch(stage_id=st["stage_id"])
        self.assertTrue(patch["ok"])
        gw.close()

        after = snapshot(git_dir=self.git_dir, work_tree=self.repo)
        assert_git_dir_unchanged(self, before, after, "no_generated_file mode")


# ---------------------------------------------------------------------------
class ApplyFootprintIsTheAuthorizedDiff(unittest.TestCase):
    """Positive control, distinguishing REQUESTED writes (permitted -- an
    authorized publish IS the point of the gateway) from UNREQUESTED ones
    (forbidden by item 1). Against a real bare publication target, an
    actually-authorized `apply` must change ONLY new git objects plus the
    one ref/reflog it was authorized to move -- nothing else in the
    git-dir: no stray lock file, no index file materializing inside a bare
    git-dir, no config/hooks change."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-pubfoot-apply-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = make_bare_target(self.tmp)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)

    def test_authorized_apply_touches_only_the_authorized_ref_and_new_objects(self):
        before = snapshot(git_dir=self.target, work_tree=None)
        base_sha = before["head"]
        self.assertIsNotNone(base_sha)

        gw = GatewayHarness(self.state_root)
        gw.set_policy(mode="zero_nightshift_file", target_git_dir=self.target,
                      target_ref="refs/heads/main", path_allowlist=["docs/**"])
        gw.add_reviewer()
        st = gw.stage()
        rv = gw.review(stage_id=st["stage_id"])
        ap = gw.approve(stage_id=st["stage_id"], review_id=rv["review_id"],
                        expected_base_sha=base_sha)
        result = gw.apply(authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
                          idempotency_key="apply-1")
        self.assertEqual(result["status"], "applied")
        gw.close()

        after = snapshot(git_dir=self.target, work_tree=None)

        # The ref legitimately moved to exactly the authorized commit.
        self.assertNotEqual(before["head"], after["head"])
        self.assertEqual(after["head"], result["commit_oid"])

        added, removed, changed = _diff_listing(before["listing"], after["listing"])
        self.assertEqual(removed, [], f"apply must never remove a git-dir file: {removed}")
        allowed_prefixes = ("objects/", "refs/heads/", "logs/")
        for path in added + changed:
            self.assertTrue(
                path.startswith(allowed_prefixes) or path == "packed-refs",
                f"unexpected git-dir artifact touched by apply: {path}",
            )
        # Nothing that looks like a lock file, an index, or a worktree
        # artifact was left behind in the (bare) git-dir.
        for entry in after["listing"]:
            self.assertFalse(entry["path"].endswith(".lock"),
                              f"apply left a lock file: {entry['path']}")
            self.assertNotEqual(entry["path"], "index",
                                 "apply must never materialize an index inside a bare git-dir")

        # Idempotent replay: same idempotency_key, zero further git-dir change.
        replay_before = snapshot(git_dir=self.target, work_tree=None)
        gw2 = GatewayHarness(self.state_root)
        replay = gw2.apply(authorization_id=ap["authorization_id"], stage_id=st["stage_id"],
                           idempotency_key="apply-1")
        gw2.close()
        self.assertEqual(replay["commit_oid"], result["commit_oid"])
        replay_after = snapshot(git_dir=self.target, work_tree=None)
        assert_git_dir_unchanged(self, replay_before, replay_after, "idempotent replay")


# ---------------------------------------------------------------------------
class BorrowedTestbedReadOnly(unittest.TestCase):
    """#400 item 1 run against the real, sealed testbed A testbed
    (623 MB .git, 856 tracked files, 0 remotes, always-failing pre-push
    hook) -- READ-ONLY, exactly as instructed: stage/review/approve/patch
    only, `apply` never called."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(BORROWED_TESTBED):
            raise unittest.SkipTest(f"sealed testbed not present: {BORROWED_TESTBED}")
        remotes = subprocess.run(
            ["git", "-C", BORROWED_TESTBED, "remote"], capture_output=True, text=True)
        if remotes.stdout.strip():
            raise unittest.SkipTest(
                "refusing to run against a testbed A testbed that has a remote configured "
                "(safety invariant: sealed testbed must have 0 remotes)")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-pubfoot-testbed A-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.git_dir = os.path.join(BORROWED_TESTBED, ".git")
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)

    def test_read_only_pipeline_leaves_testbed_untouched(self):
        before = snapshot(git_dir=self.git_dir, work_tree=BORROWED_TESTBED)
        before_prov = prov.capture_footprint(BORROWED_TESTBED, tier="A")

        head = _run_git_dir(self.git_dir, BORROWED_TESTBED, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head, "4c89a82ff47f6104ebd180ef82b94a5393f7fe37")

        gw = GatewayHarness(self.state_root, tenant_id=1002, repository_id=1)
        gw.set_policy(mode="zero_nightshift_file", target_git_dir=self.git_dir,
                      target_ref="refs/heads/master", path_allowlist=["docs/**"])
        gw.add_reviewer()
        st = gw.stage(content=b"nightshift read-only footprint probe\n",
                      path="docs/NIGHTSHIFT_TEST_PROBE.md")
        self.assertEqual(st["status"], "staged")
        rv = gw.review(stage_id=st["stage_id"])
        self.assertEqual(rv["verdict"], "approve")
        ap = gw.approve(stage_id=st["stage_id"], review_id=rv["review_id"],
                        expected_base_sha=head)
        self.assertTrue(ap["ok"])
        patch = gw.patch(stage_id=st["stage_id"])
        self.assertIn("NIGHTSHIFT_TEST_PROBE.md", patch["patch"])
        # Explicitly never called: gw.apply(...). Read-only means read-only.
        gw.close()

        after = snapshot(git_dir=self.git_dir, work_tree=BORROWED_TESTBED)
        after_prov = prov.capture_footprint(BORROWED_TESTBED, tier="A")
        assert_git_dir_unchanged(self, before, after, "testbed A testbed, read-only pipeline")
        cmp = prov.compare_footprint(before_prov, after_prov)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])

        remotes_after = subprocess.run(
            ["git", "-C", BORROWED_TESTBED, "remote"], capture_output=True, text=True)
        self.assertEqual(remotes_after.stdout.strip(), "", "testbed must still have 0 remotes")


# ---------------------------------------------------------------------------
class CompareFootprintDetectsDirt(unittest.TestCase):
    """`prov.compare_footprint` is PG-4's production oracle -- its own
    docstring calls it "the acceptance proof / `publication footprint`
    verb" -- yet every call site above (this file's own `assert_git_dir_
    unchanged` proves the invariant independently) only ever feeds it a
    PRISTINE before/after pair and asserts verdict=='clean'. A comparator
    that always returned 'clean', or one silently narrowed to exactly the
    git-status-only check item 1 explicitly forbids relying on, would be
    indistinguishable from the real one at every one of those sites. These
    two feed it a genuinely dirty pair directly, never through the
    gateway, to prove it actually has teeth."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-pubfoot-dirty-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_worktree_repo(self.tmp)
        self.target = os.path.join(self.repo, ".git")

    def test_stray_file_planted_inside_gitdir_is_reported_as_a_violation(self):
        """The 'added' case -- and the exact reproduction named against
        this module: a stray file inside .git."""
        before = prov.capture_footprint(self.repo, tier="B")
        with open(os.path.join(self.target, "NIGHTSHIFT_STRAY"), "wb") as fh:
            fh.write(b"planted directly on disk, never through the gateway\n")
        after = prov.capture_footprint(self.repo, tier="B")

        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "violation", cmp)
        self.assertIn(
            {"manifest": "gitdir", "path": "NIGHTSHIFT_STRAY", "kind": "added"},
            cmp["violations"])

    def test_byte_changed_inside_an_existing_gitdir_file_is_reported_as_a_violation(self):
        """The 'changed' case, and specifically NOT reachable via
        `git status --porcelain` -- this is a bare repository (no work
        tree at all) and `description` is a file `git status` never looks
        at, so a comparator quietly narrowed to a git-status-only check
        would report this pair as clean."""
        before = prov.capture_footprint(self.repo, tier="B")
        desc_path = os.path.join(self.target, "description")
        self.assertTrue(os.path.exists(desc_path), "fixture assumption: git init writes description")
        with open(desc_path, "ab") as fh:
            fh.write(b"nightshift-test-tamper\n")
        after = prov.capture_footprint(self.repo, tier="B")

        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "violation", cmp)
        changed = [v for v in cmp["violations"]
                   if v["manifest"] == "gitdir" and v["path"] == "description"
                   and v["kind"] == "changed"]
        self.assertEqual(len(changed), 1, cmp["violations"])

    def test_pristine_pair_is_still_clean(self):
        """Sibling control: without these two tests, a comparator that
        always returns 'violation' would look just as broken as one that
        always returns 'clean' -- but nothing here would catch it. Proves
        this suite can tell the two failure modes apart."""
        before = prov.capture_footprint(self.repo, tier="B")
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])


if __name__ == "__main__":
    unittest.main()
