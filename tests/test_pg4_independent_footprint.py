"""Issue #695 item 6 -- PG-4's zero-footprint claim, re-proven from
scratch by a reviewer with no authorship stake in the original harness.

Nothing in this file calls `prov.capture_footprint` / `prov.compare_footprint`
to decide whether a footprint was left. The four probes below are written
independently, from the plan's own wording ("Compare `git status
--porcelain`, `git rev-parse HEAD`, and a `find -newer` sweep before/after"
plus a plain listing), and are deliberately STRICTER than the production
oracle in one respect: probe 4 fails on an mtime-only touch, which
`compare_footprint` classifies as REPORT rather than FAIL.

  probe 1  `git status --porcelain=v1 -z -uall --ignored=matching`
  probe 2  `git rev-parse HEAD` + the full ref list (`for-each-ref`)
  probe 3  a recursive manifest of EVERY entry under the git dir and the
           work tree -- path, type, mode, size, st_nlink, symlink target,
           and the sha256 of every file's contents. This is the recursive
           .git CONTENT hash; probes 1 and 2 cannot see inside .git at all.
  probe 4  `find <repo> -newer <marker>` (mtime sweep) + `find <repo>` (a
           plain sorted path listing)

Probes 1 and 2 shell out to git, and git is empirically able to rewrite
`.git/index`'s stat cache on a read command, so those two run OUTSIDE the
marker-bounded window: only the operations under test sit inside it, and
only their footprint can appear in the probe-4 sweep.

Positive controls come first (`TheProbeItselfHasTeeth`): five planted
mutations, each one proving a specific probe fires -- including the two
shapes a git-status-only check is blind to (a same-SIZE byte change inside
`.git`, and an st_nlink change with identical bytes).

`ProductionOracleGapsFoundByMutation` closes the gaps this audit found by
injecting named defects into `bin/nightshift_provenance.py` and watching
the pre-existing 124-test suite stay green. Each injection, and the result
BEFORE this file existed:

  PG4-A  `walk_manifest`: `entry["sha256"] = _sha256_file(full)` removed.
         124 tests, OK. The existing "changed" test appends bytes to
         `.git/description`, so `size` alone catches it and the content
         hash -- the whole point of a recursive .git proof -- is never
         load-bearing.
  PG4-B  `walk_manifest`: `"nlink": st.st_nlink` removed. 124 tests, OK --
         even though the module's own docstring names st_nlink as
         [VERIFIED] the only field that catches `git clone --bare`
         hardlinking customer loose objects (E2).
  PG4-D  `compare_footprint`: the `removed` branch stops appending a
         violation. 124 tests, OK -- deletion inside `.git` was unproven.
  PG4-F  `compare_footprint`: the git-view drift loop stops appending a
         violation. 124 tests, OK -- every existing call site feeds it a
         pristine pair, so the whole corroborating-views half of the
         oracle was untested.
  PG4-H  `compare_footprint`: the `_diff_manifest("worktree", ...)` call
         deleted. 124 tests, OK.
  PG4-I  `capture_footprint`: the `footprint_capture_invalid` raise
         deleted. 124 tests, OK -- the self-proof was only ever read on a
         clean run.
  PG4-M  `_GIT_VIEW_COMMANDS["status"]` loses `--ignored=matching`. 124
         tests, OK -- the E6a/E6b tests exercise `detect_dirty`, which
         builds its own argv, not the footprint view's.
  PG4-P  `capture_footprint`: tier B's worktree pass forced to
         `hash_files=False`. 124 tests, OK.
  PG4-Q  `capture_footprint`: `common_after = None`. 124 tests, OK --
         every fixture in the suite is an ordinary checkout where
         common_dir == gitdir.

Two injections are recorded here as NOT-a-gap, because the behaviour
genuinely does not change:

  PG4-N  `GIT_OPTIONAL_LOCKS=0` removed from `_READ_ONLY_ENV_OVERRIDES`.
  PG4-O  `--no-optional-locks` removed from customer_git's argv.
         Each ALONE leaves the index untouched, because the other still
         holds -- they are two independent implementations of one control,
         not one control and some dead code. Removing BOTH is caught, by
         `test_pg4no_the_guarded_status_does_not_rewrite_a_stale_index`
         AND by the four-probe default-mode test.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import stat
import subprocess
import traceback
import sys
import tempfile
import time
import unittest
from pathlib import Path
from publication_identities import producer_token  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
TESTBED = Path("/home/operator/testbeds/.nightshift-testbed/testbed A")

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               GIT_TERMINAL_PROMPT="0")


_IMPORT_ERROR = None

def _load(name, path):
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        global _IMPORT_ERROR
        _IMPORT_ERROR = traceback.format_exc()
        print(f"WARNING: {path} failed to import: {exc}", file=sys.stderr)
        return None
    finally:
        sys.path.remove(str(BIN))
    return module


prov = _load("nightshift_provenance", BIN / "nightshift_provenance.py")
pub = _load("nightshift_publication", BIN / "nightshift_publication.py")
SKIP = "bin/nightshift_provenance.py not importable"

T0 = 2_000_000.0


# ---------------------------------------------------------------------------
# The four probes. Written from the plan text, not from the production
# harness -- no import of capture_footprint/compare_footprint anywhere.
# ---------------------------------------------------------------------------

def _git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=True, env=GIT_ENV, timeout=120)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr.decode('utf-8','replace')}")
    return r


def probe_1_status(repo):
    return _git(repo, "status", "--porcelain=v1", "-z", "-uall",
                "--ignored=matching").stdout


def probe_2_head_and_refs(repo):
    head = _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False)
    refs = _git(repo, "for-each-ref", "--format=%(objectname) %(objecttype) %(refname)")
    return (head.stdout.strip(), refs.stdout)


def probe_3_recursive_manifest(root):
    """Every entry beneath `root`, directories included, with the sha256 of
    every regular file's full contents. Deterministic ordering; mtime is
    recorded separately (probe 4 owns mtime) so a same-bytes rewrite is
    visible as a probe-4 finding rather than hiding here."""
    root = os.path.realpath(root)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(dirnames) + sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel in out:
                continue
            st = os.lstat(full)
            e = {"mode": oct(stat.S_IMODE(st.st_mode)), "nlink": st.st_nlink}
            if stat.S_ISLNK(st.st_mode):
                e["type"] = "symlink"
                e["target"] = os.readlink(full)
            elif stat.S_ISDIR(st.st_mode):
                e["type"] = "dir"
            elif stat.S_ISREG(st.st_mode):
                e["type"] = "file"
                e["size"] = st.st_size
                h = hashlib.sha256()
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                e["sha256"] = h.hexdigest()
            else:
                e["type"] = "other"
            out[rel] = e
    return out


def probe_4_paths(repo):
    paths = []
    for dirpath, dirnames, filenames in os.walk(repo, followlinks=False):
        dirnames.sort()
        for name in sorted(dirnames) + sorted(filenames):
            paths.append(os.path.relpath(os.path.join(dirpath, name), repo))
    return sorted(paths)


def probe_4_newer_sweep(repo, marker):
    r = subprocess.run(["find", str(repo), "-newer", str(marker)],
                       capture_output=True, text=True, timeout=120, check=True)
    return sorted(ln for ln in r.stdout.splitlines() if ln.strip())


def snapshot(repo, git_dir):
    """Probes 1-3 plus the plain listing. Probe 4's mtime sweep is
    marker-bounded and therefore taken by the caller, not here."""
    return {
        "status": probe_1_status(repo),
        "head_refs": probe_2_head_and_refs(repo),
        "gitdir": probe_3_recursive_manifest(git_dir),
        "worktree_paths": probe_4_paths(repo),
    }


def diff_manifest(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in (set(before) & set(after)) if before[p] != after[p])
    return {"added": added, "removed": removed, "changed": changed}


class FourProbeMixin:

    def assert_no_footprint(self, before, after, msg=""):
        self.assertEqual(before["status"], after["status"],
                         f"{msg}probe 1 (git status --porcelain) changed")
        self.assertEqual(before["head_refs"], after["head_refs"],
                         f"{msg}probe 2 (rev-parse HEAD / for-each-ref) changed")
        d = diff_manifest(before["gitdir"], after["gitdir"])
        self.assertEqual(d, {"added": [], "removed": [], "changed": []},
                         f"{msg}probe 3 (recursive .git content hash) changed: {d}")
        self.assertEqual(before["worktree_paths"], after["worktree_paths"],
                         f"{msg}probe 4a (plain path listing) changed")


# ---------------------------------------------------------------------------

def build_real_repo(base, name="customer"):
    """A real repository with the shapes that defeat a naive probe: a
    packed history, loose objects, an ignored file, an untracked file, a
    symlink, and a nested directory."""
    repo = os.path.join(base, name)
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "audit@test.invalid")
    _git(repo, "config", "user.name", "audit")
    os.makedirs(os.path.join(repo, "src"))
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("seed readme\n")
    with open(os.path.join(repo, "src", "main.py"), "w") as fh:
        fh.write("print('hi')\n")
    with open(os.path.join(repo, ".gitignore"), "w") as fh:
        fh.write("build/\n")
    _git(repo, "add", "-A")
    env = dict(GIT_ENV, GIT_AUTHOR_NAME="audit", GIT_AUTHOR_EMAIL="audit@test.invalid",
               GIT_COMMITTER_NAME="audit", GIT_COMMITTER_EMAIL="audit@test.invalid",
               GIT_AUTHOR_DATE="@1700000000 +0000", GIT_COMMITTER_DATE="@1700000000 +0000")
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "seed"],
                   check=True, capture_output=True, env=env)
    # packed history + loose objects afterwards, so both storage shapes exist
    _git(repo, "gc", "-q")
    with open(os.path.join(repo, "src", "later.py"), "w") as fh:
        fh.write("later = True\n")
    _git(repo, "add", "src/later.py")
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "second"],
                   check=True, capture_output=True, env=env)
    os.makedirs(os.path.join(repo, "build"), exist_ok=True)
    with open(os.path.join(repo, "build", "artifact.o"), "w") as fh:
        fh.write("ignored build output\n")
    with open(os.path.join(repo, "UNTRACKED.txt"), "w") as fh:
        fh.write("untracked\n")
    os.symlink("README.md", os.path.join(repo, "link-to-readme"))
    return repo


def build_seeded_bare(base, name="dest.git"):
    """A real bare destination with one commit on refs/heads/main, built
    with plumbing only. The gateway needs a resolvable base to diff
    against; an empty bare repo refuses with `patch_base_unavailable`
    before any of the read path under test has run."""
    dest = os.path.join(base, name)
    subprocess.run(["git", "init", "-q", "--bare", dest],
                   check=True, capture_output=True, env=GIT_ENV)
    def g(*args, stdin=None):
        r = subprocess.run(["git", "--git-dir", dest] + list(args), input=stdin,
                           capture_output=True, env=GIT_ENV, check=True)
        return r.stdout.decode().strip()
    blob = g("hash-object", "-w", "--stdin", stdin=b"dest seed\n")
    idx = os.path.join(base, "dest-seed-index")
    env = dict(GIT_ENV, GIT_INDEX_FILE=idx,
               GIT_AUTHOR_NAME="audit", GIT_AUTHOR_EMAIL="audit@test.invalid",
               GIT_COMMITTER_NAME="audit", GIT_COMMITTER_EMAIL="audit@test.invalid",
               GIT_AUTHOR_DATE="@1700000000 +0000", GIT_COMMITTER_DATE="@1700000000 +0000")
    subprocess.run(["git", "--git-dir", dest, "update-index", "--add",
                    "--cacheinfo", f"100644,{blob},SEED.md"],
                   check=True, capture_output=True, env=env)
    tree = subprocess.run(["git", "--git-dir", dest, "write-tree"], check=True,
                          capture_output=True, env=env).stdout.decode().strip()
    commit = subprocess.run(["git", "--git-dir", dest, "commit-tree", tree, "-m", "seed"],
                            check=True, capture_output=True, env=env).stdout.decode().strip()
    subprocess.run(["git", "--git-dir", dest, "update-ref", "refs/heads/main", commit],
                   check=True, capture_output=True, env=GIT_ENV)
    os.path.exists(idx) and os.remove(idx)
    return dest


def _loose_object_paths(git_dir):
    base = os.path.join(git_dir, "objects")
    found = []
    for d in sorted(os.listdir(base)):
        if len(d) != 2:
            continue
        sub = os.path.join(base, d)
        if not os.path.isdir(sub):
            continue
        for f in sorted(os.listdir(sub)):
            found.append(os.path.join(sub, f))
    return found


# ---------------------------------------------------------------------------
@unittest.skipIf(prov is None, SKIP)
class TheProbeItselfHasTeeth(unittest.TestCase, FourProbeMixin):
    """Before the probe is allowed to certify anything, it has to be shown
    to fail on planted dirt. Without this class, a probe that always
    returned "identical" would certify a repository that had been
    rewritten."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-pg4-audit-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = build_real_repo(self.tmp)
        self.git_dir = os.path.join(self.repo, ".git")
        self.before = snapshot(self.repo, self.git_dir)

    def test_control_an_untouched_repository_reads_identical(self):
        self.assert_no_footprint(self.before, snapshot(self.repo, self.git_dir))

    def test_same_size_byte_change_inside_gitdir_is_caught_by_probe_3_only(self):
        """The shape a git-status probe and a size-only manifest are both
        blind to: one byte flipped, length unchanged, inside `.git`."""
        target = os.path.join(self.git_dir, "description")
        with open(target, "rb") as fh:
            data = bytearray(fh.read())
        self.assertTrue(data, "fixture assumption: git init writes .git/description")
        data[0] = data[0] ^ 0x20
        with open(target, "wb") as fh:
            fh.write(bytes(data))
        after = snapshot(self.repo, self.git_dir)
        self.assertEqual(self.before["status"], after["status"],
                         "fixture assumption: git status cannot see this")
        self.assertEqual(self.before["head_refs"], after["head_refs"])
        self.assertEqual(
            self.before["gitdir"]["description"]["size"],
            after["gitdir"]["description"]["size"],
            "fixture assumption: the change must be size-neutral")
        self.assertIn("description", diff_manifest(self.before["gitdir"],
                                                   after["gitdir"])["changed"])

    def test_hardlinking_a_loose_object_is_caught_by_nlink(self):
        """E2: `git clone --bare` hardlinks customer loose objects. Bytes,
        size, mode and mtime are all untouched; only st_nlink moves."""
        loose = _loose_object_paths(self.git_dir)
        self.assertTrue(loose, "fixture assumption: the second commit left loose objects")
        victim = loose[0]
        rel = os.path.relpath(victim, self.git_dir).replace(os.sep, "/")
        os.link(victim, os.path.join(self.tmp, "hardlink-outside"))
        after = snapshot(self.repo, self.git_dir)
        self.assertEqual(self.before["status"], after["status"])
        self.assertEqual(self.before["gitdir"][rel]["sha256"],
                         after["gitdir"][rel]["sha256"],
                         "fixture assumption: the bytes must be untouched")
        self.assertEqual(self.before["gitdir"][rel]["size"],
                         after["gitdir"][rel]["size"])
        self.assertIn(rel, diff_manifest(self.before["gitdir"], after["gitdir"])["changed"])
        self.assertEqual(after["gitdir"][rel]["nlink"],
                         self.before["gitdir"][rel]["nlink"] + 1)

    def test_a_deleted_file_under_gitdir_is_caught(self):
        os.remove(os.path.join(self.git_dir, "description"))
        after = snapshot(self.repo, self.git_dir)
        self.assertIn("description",
                      diff_manifest(self.before["gitdir"], after["gitdir"])["removed"])

    def test_a_planted_file_under_gitdir_is_caught(self):
        with open(os.path.join(self.git_dir, "NIGHTSHIFT_STRAY"), "wb") as fh:
            fh.write(b"planted\n")
        after = snapshot(self.repo, self.git_dir)
        self.assertIn("NIGHTSHIFT_STRAY",
                      diff_manifest(self.before["gitdir"], after["gitdir"])["added"])

    def test_an_mtime_only_touch_is_caught_by_the_find_newer_sweep(self):
        """Probe 4 is the one probe that sees a same-bytes rewrite. Proving
        it fires here is what makes its silence meaningful below."""
        marker = os.path.join(self.tmp, "marker-teeth")
        open(marker, "w").close()
        time.sleep(1.1)
        target = os.path.join(self.git_dir, "description")
        with open(target, "rb") as fh:
            data = fh.read()
        with open(target, "wb") as fh:
            fh.write(data)  # identical bytes, new mtime
        touched = probe_4_newer_sweep(self.repo, marker)
        self.assertTrue(any(t.endswith("/description") for t in touched), touched)


# ---------------------------------------------------------------------------
@unittest.skipIf(prov is None or pub is None, SKIP)
class DefaultModeWritesNothingIntoTheCustomerRepository(unittest.TestCase, FourProbeMixin):
    """The PG-4 claim itself, on a real repository, under the four probes
    above. `zero_nightshift_file` is the default (non-strict) policy mode:
    the mode in which the gateway is ALLOWED to publish, and therefore the
    mode in which "it still wrote nothing into the source checkout" is a
    claim worth proving. The destination bare repo is a separate
    repository; the customer checkout here is only ever a SOURCE."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-pg4-default-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = build_real_repo(self.tmp)
        self.git_dir = os.path.join(self.repo, ".git")
        self.dest = build_seeded_bare(self.tmp)
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.state_root, mode=0o700)

    def _run_full_default_mode_cycle(self):
        tenant_id, repository_id = 4001, 1
        prov_cx = prov.connect(self.state_root, tenant_id, repository_id)
        self.addCleanup(prov_cx.close)

        # provenance read path, all of it, against the real checkout
        fingerprint = prov.repository_fingerprint(self.repo)
        roots = prov.resolve_customer_roots(self.repo)
        prov.collect_git_views(self.repo)
        prov.capture_footprint(self.repo, tier="A")
        prov.capture_footprint(self.repo, tier="B")
        prov.discover_closure(self.repo)
        prov.detect_dirty(self.repo)
        head = _git(self.repo, "rev-parse", "HEAD").stdout.decode().strip()
        tree = _git(self.repo, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
        prov.record_source_snapshot(prov_cx, tenant_id, repository_id, "snap-audit", {
            "repository_fingerprint": fingerprint, "object_format": "sha1",
            "commit_oid": head, "tree_oid": tree, "captured_at": T0})

        # publication path in DEFAULT mode, up to and including approve
        root = pub.resolve_publication_root(self.state_root, tenant_id, repository_id)
        cx = pub.connect(root)
        self.addCleanup(cx.close)
        pub.policy_set(cx, tenant_id=tenant_id, repository_id=repository_id,
                       mode="zero_nightshift_file", path_allowlist=["**"],
                       target_git_dir=self.dest, target_ref="refs/heads/main",
                       key_version=None, t=T0)
        pub.register_reviewer(cx, tenant_id=tenant_id, reviewer_identity="eng_bob",
                              token_bytes=b"tok-bob", t=T0)
        st = pub.stage(cx, tenant_id=tenant_id, repository_id=repository_id,
                       content_bytes=b"audit report body\n",
                       destination_path="docs/AUDIT.md", classification="report",
                       producer_identity="eng_alice",
                       producer_token_bytes=producer_token(pub, cx, tenant_id, "eng_alice"), producer_run_id="run-audit",
                       snapshot_id="snap-audit", t=T0)
        rv = pub.review(cx, tenant_id=tenant_id, repository_id=repository_id,
                        stage_id=st["stage_id"], reviewer_identity="eng_bob",
                        reviewer_run_id="rev-audit", token_bytes=b"tok-bob",
                        verdict="approve", t=T0)
        pub.patch_stage(cx, stage_id=st["stage_id"], tenant_id=tenant_id,
                        repository_id=repository_id)
        self.assertEqual(rv["verdict"], "approve")
        self.assertIsNotNone(roots["worktree_root"])

    def test_the_full_default_mode_cycle_leaves_the_customer_repo_byte_identical(self):
        before = snapshot(self.repo, self.git_dir)

        marker = os.path.join(self.tmp, "marker")
        open(marker, "w").close()
        time.sleep(1.1)

        self._run_full_default_mode_cycle()

        touched = probe_4_newer_sweep(self.repo, marker)
        after = snapshot(self.repo, self.git_dir)

        self.assert_no_footprint(before, after)
        self.assertEqual(touched, [],
                         "probe 4: `find -newer` saw the default-mode cycle touch "
                         f"the customer repository: {touched!r}")

    def test_the_state_root_is_outside_the_customer_repository(self):
        """The other half of "writes nothing here": everything it DID write
        has to be somewhere else. A state_root that resolved under the
        customer checkout would make the probe above pass for the wrong
        reason on a differently-configured deployment."""
        self._run_full_default_mode_cycle()
        wrote = [p for p in probe_4_paths(self.repo) if "nightshift" in p.lower()]
        self.assertEqual(wrote, [], f"nightshift-named paths inside the customer repo: {wrote}")
        self.assertTrue(os.path.isdir(os.path.join(self.state_root, "tenants")),
                        "state_root did not receive the tenant-scoped tree")
        self.assertFalse(
            os.path.realpath(self.state_root).startswith(os.path.realpath(self.repo) + os.sep))


# ---------------------------------------------------------------------------
@unittest.skipIf(prov is None, SKIP)
class ProductionOracleGapsFoundByMutation(unittest.TestCase):
    """Four defects injected into `bin/nightshift_provenance.py` that the
    pre-existing 124-test suite did not catch. Each test below is the
    smallest thing that turns one of them red -- see the module docstring
    for the exact injection and the recorded before-result."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ns-pg4-gaps-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = build_real_repo(self.tmp)
        self.git_dir = os.path.join(self.repo, ".git")

    def _violations(self, before, after, path):
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "violation", cmp)
        return [v for v in cmp["violations"]
                if v["manifest"] == "gitdir" and v["path"] == path]

    def test_pg4a_same_size_byte_change_inside_gitdir_is_a_violation(self):
        """PG4-A. The existing "changed" test APPENDS to .git/description,
        so `size` alone catches it; delete the sha256 and nothing goes red.
        This one flips a byte in place: size, mode and st_nlink are all
        unchanged, so only the content hash can see it."""
        before = prov.capture_footprint(self.repo, tier="B")
        target = os.path.join(self.git_dir, "description")
        with open(target, "rb") as fh:
            data = bytearray(fh.read())
        size_before = len(data)
        data[0] = data[0] ^ 0x20
        with open(target, "wb") as fh:
            fh.write(bytes(data))
        self.assertEqual(os.path.getsize(target), size_before,
                         "this test is only meaningful if the size is unchanged")
        after = prov.capture_footprint(self.repo, tier="B")

        hits = self._violations(before, after, "description")
        self.assertEqual(len(hits), 1, prov.compare_footprint(before, after)["violations"])
        self.assertEqual(hits[0]["kind"], "changed")
        self.assertNotEqual(hits[0]["before"]["sha256"], hits[0]["after"]["sha256"])
        self.assertEqual(hits[0]["before"]["size"], hits[0]["after"]["size"],
                         "size must NOT be what made this a violation")

    def test_pg4a_the_worktree_half_of_tier_b_also_hashes_content(self):
        """Same gap, worktree side: tier B's whole reason to exist is the
        worktree CONTENT pass. A same-size edit to a tracked file must be a
        violation on the worktree manifest, not only on git status."""
        before = prov.capture_footprint(self.repo, tier="B")
        target = os.path.join(self.repo, "README.md")
        with open(target, "rb") as fh:
            data = bytearray(fh.read())
        size_before = len(data)
        data[0] = data[0] ^ 0x20
        with open(target, "wb") as fh:
            fh.write(bytes(data))
        self.assertEqual(os.path.getsize(target), size_before)
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        hits = [v for v in cmp["violations"]
                if v["manifest"] == "worktree" and v["path"] == "README.md"]
        self.assertEqual(len(hits), 1, cmp["violations"])
        self.assertEqual(hits[0]["before"]["size"], hits[0]["after"]["size"])
        self.assertNotEqual(hits[0]["before"]["sha256"], hits[0]["after"]["sha256"])

    def test_pg4b_hardlinking_a_loose_object_is_a_violation_via_nlink(self):
        """PG4-B, and E2 in the module's own docstring: `git clone --bare`
        hardlinks customer loose objects. Bytes, size, mode and mtime are
        identical afterwards -- st_nlink is the only field that moves."""
        before = prov.capture_footprint(self.repo, tier="B")
        loose = _loose_object_paths(self.git_dir)
        self.assertTrue(loose, "fixture assumption: loose objects exist")
        victim = loose[0]
        rel = os.path.relpath(victim, self.git_dir).replace(os.sep, "/")
        os.link(victim, os.path.join(self.tmp, "hardlink-outside"))
        after = prov.capture_footprint(self.repo, tier="B")

        hits = self._violations(before, after, rel)
        self.assertEqual(len(hits), 1, prov.compare_footprint(before, after)["violations"])
        self.assertEqual(hits[0]["kind"], "changed")
        self.assertEqual(hits[0]["before"]["sha256"], hits[0]["after"]["sha256"],
                         "content must NOT be what made this a violation")
        self.assertEqual(hits[0]["before"]["size"], hits[0]["after"]["size"])
        self.assertEqual(hits[0]["after"]["nlink"], hits[0]["before"]["nlink"] + 1)

    def test_pg4d_a_file_removed_from_gitdir_is_a_violation(self):
        """PG4-D. Only the `added` and `changed` branches were proven; a
        deletion inside `.git` -- a pruned ref, a removed loose object --
        was not."""
        before = prov.capture_footprint(self.repo, tier="B")
        os.remove(os.path.join(self.git_dir, "description"))
        after = prov.capture_footprint(self.repo, tier="B")
        hits = self._violations(before, after, "description")
        self.assertEqual(hits, [{"manifest": "gitdir", "path": "description",
                                 "kind": "removed"}])

    def test_pg4f_git_view_drift_alone_is_a_violation(self):
        """PG4-F. Every existing call site hands compare_footprint a
        pristine pair, so the corroborating-views half never had to work.
        compare_footprint is a pure function of two dicts, so the cleanest
        proof feeds it one -- a pair identical everywhere except one view's
        output, which is exactly the shape of a ref that moved and moved
        back on disk."""
        pristine = prov.capture_footprint(self.repo, tier="A")
        drifted = dict(pristine)
        views = dict(pristine["git_views"])
        views["refs"] = views["refs"] + "0" * 40 + " commit refs/heads/injected\n"
        drifted["git_views"] = views

        cmp = prov.compare_footprint(pristine, drifted)
        self.assertEqual(cmp["verdict"], "violation", cmp)
        self.assertEqual(
            [v for v in cmp["violations"] if v["manifest"] == "git_view"][0]["path"],
            "refs")
        self.assertEqual([v for v in cmp["violations"] if v["manifest"] != "git_view"], [],
                         "only the view may differ -- the manifests are the same object")

    def test_pg4f_every_corroborating_view_is_compared_not_just_one(self):
        """The same gap one level up: a comparator that only diffed
        `status` would pass the test above if it happened to pick `status`.
        Drift each view in turn."""
        pristine = prov.capture_footprint(self.repo, tier="A")
        self.assertGreaterEqual(len(pristine["git_views"]), 10, pristine["git_views"].keys())
        for name in sorted(pristine["git_views"]):
            with self.subTest(view=name):
                drifted = dict(pristine)
                views = dict(pristine["git_views"])
                views[name] = (views[name] or "") + "\ndrift\n"
                drifted["git_views"] = views
                cmp = prov.compare_footprint(pristine, drifted)
                self.assertEqual(cmp["verdict"], "violation", name)
                self.assertIn(name, [v["path"] for v in cmp["violations"]
                                     if v["manifest"] == "git_view"])

    def test_pg4h_the_worktree_manifest_is_actually_compared(self):
        """PG4-H. INJECT: delete the `_diff_manifest("worktree", ...)` call
        in compare_footprint. 124 tests, OK -- the worktree half of the
        comparison had no test of its own, because every worktree change
        the existing suite makes also lands in `.git/index` via `git add`.
        This one never touches the index."""
        before = prov.capture_footprint(self.repo, tier="B")
        with open(os.path.join(self.repo, "PLANTED.txt"), "w") as fh:
            fh.write("written straight into the work tree, never staged\n")
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "violation", cmp)
        self.assertIn({"manifest": "worktree", "path": "PLANTED.txt", "kind": "added"},
                      cmp["violations"])
        self.assertEqual([v for v in cmp["violations"] if v["manifest"] == "gitdir"], [],
                         "fixture assumption: nothing under .git may have moved, or "
                         "the gitdir diff could be what caught this")

    def test_pg4i_capture_refuses_its_own_evidence_if_its_own_step_perturbed_the_repo(self):
        """PG4-I. INJECT: delete the `raise ProvenanceError(
        "footprint_capture_invalid", ...)` in capture_footprint. 124 tests,
        OK -- `self_proof_clean` was only ever read on a clean run, so the
        branch that refuses a poisoned capture was never taken.

        The seam patched here is `collect_git_views`, the step capture
        brackets between its two `.git` walks -- i.e. exactly the thing the
        self-proof measures. The guard itself is untouched."""
        real_collect = prov.collect_git_views
        stray = os.path.join(self.git_dir, "CAPTURE_SIDE_EFFECT")

        def perturbing(customer_path):
            views = real_collect(customer_path)
            with open(stray, "wb") as fh:
                fh.write(b"a git view command wrote this\n")
            return views

        prov.collect_git_views = perturbing
        try:
            with self.assertRaises(prov.ProvenanceError) as ctx:
                prov.capture_footprint(self.repo, tier="B")
        finally:
            prov.collect_git_views = real_collect
        self.assertEqual(ctx.exception.reason_code, "footprint_capture_invalid")

        # Control: with the seam restored (and the stray removed) the very
        # same call succeeds, so the refusal above came from the
        # perturbation and not from the patching.
        os.remove(stray)
        self.assertTrue(prov.capture_footprint(self.repo, tier="B")["self_proof_clean"])

    def test_pg4m_the_footprint_status_view_reports_ignored_files(self):
        """PG4-M. INJECT: drop `--ignored=matching` from
        `_GIT_VIEW_COMMANDS["status"]`. 124 tests, OK. The E6a/E6b tests
        that prove `--ignored=matching` matters exercise `detect_dirty`,
        which builds its own argv -- the footprint VIEW's argv was
        unproven, so the corroborating status view could go blind to
        exactly the `.omni/`-shaped drop the plan names."""
        with open(os.path.join(self.git_dir, "info", "exclude"), "a") as fh:
            fh.write(".omni/\n")
        os.makedirs(os.path.join(self.repo, ".omni"), exist_ok=True)
        with open(os.path.join(self.repo, ".omni", "state.json"), "w") as fh:
            fh.write("{}\n")

        views = prov.capture_footprint(self.repo, tier="A")["git_views"]
        self.assertIn(".omni", views["status"],
                      "the footprint status view must be taken with "
                      "--ignored=matching, or an ignored Nightshift directory "
                      "is invisible to it")

    def test_pg4no_the_guarded_status_does_not_rewrite_a_stale_index(self):
        """PG4-N / PG4-O. `GIT_OPTIONAL_LOCKS=0` in the env and
        `--no-optional-locks` on argv are two independent implementations
        of ONE control: injected separately, neither changes behaviour
        (124 tests OK, and this test OK too -- correctly, since the index
        really is not rewritten). Injected TOGETHER, this test goes red.
        That is the honest claim: a redundant pair, not a guard plus dead
        code.

        The pre-existing test states outright that it does not assert the
        E1 trigger "to avoid a flaky test" -- it runs the unguarded command
        and discards the result -- so it proves only that the guarded call
        is clean when nothing would have rewritten the index anyway. This
        makes the trigger deterministic instead of avoiding it: bump the
        mtime of every tracked file (contents untouched) so the index stat
        cache is definitely stale, then assert BOTH directions -- the
        guarded call leaves the index byte-identical, and an unguarded
        `git status` in the very same state rewrites it."""
        for name in ("README.md", ".gitignore"):
            os.utime(os.path.join(self.repo, name))
        os.utime(os.path.join(self.repo, "src", "main.py"))
        os.utime(os.path.join(self.repo, "src", "later.py"))
        index = os.path.join(self.git_dir, "index")

        def fingerprint():
            with open(index, "rb") as fh:
                return (hashlib.sha256(fh.read()).hexdigest(), os.stat(index).st_mtime_ns)

        base = fingerprint()
        prov.customer_git(self.repo, ["status", "--porcelain=v1", "-z", "-uall",
                                      "--ignored=matching"])
        self.assertEqual(fingerprint(), base,
                         "the guarded status rewrote .git/index -- "
                         "--no-optional-locks / GIT_OPTIONAL_LOCKS=0 is not in effect")

        _git(self.repo, "status", "--porcelain")
        self.assertNotEqual(
            fingerprint(), base,
            "control failed: an UNGUARDED `git status` did not rewrite the stale "
            "index either, so this test could not have detected the guard's "
            "removal. Investigate rather than relaxing the assertion above.")

    def test_pg4q_the_common_dir_is_walked_when_it_differs_from_the_gitdir(self):
        """PG4-Q. INJECT: `common_after = None` in capture_footprint. 124
        tests, OK -- every fixture in the suite is an ordinary checkout
        where common_dir == gitdir, so `common_manifest` was always None
        and the whole COMMON half of the proof was dead weight in the
        tests. It is not dead in production: on a LINKED WORKTREE the
        gitdir is `<main>/.git/worktrees/<name>` and the object store --
        every submodule's included, per the module's own docstring -- lives
        in COMMON, outside the gitdir this walk would otherwise cover."""
        linked = os.path.join(self.tmp, "linked-wt")
        _git(self.repo, "worktree", "add", "-q", linked, "-b", "audit-branch")

        roots = prov.resolve_customer_roots(linked)
        self.assertNotEqual(roots["gitdir"], roots["common_dir"],
                            "fixture assumption: a linked worktree must split "
                            "gitdir from common_dir")

        before = prov.capture_footprint(linked, tier="A")
        self.assertIsNotNone(before["common_manifest"],
                             "capture_footprint did not walk the common dir at all")
        with open(os.path.join(roots["common_dir"], "COMMON_STRAY"), "wb") as fh:
            fh.write(b"planted in the shared object store, invisible to the gitdir walk\n")
        after = prov.capture_footprint(linked, tier="A")

        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "violation", cmp)
        self.assertIn({"manifest": "common", "path": "COMMON_STRAY", "kind": "added"},
                      cmp["violations"])

    def test_control_a_pristine_pair_is_still_clean(self):
        before = prov.capture_footprint(self.repo, tier="B")
        after = prov.capture_footprint(self.repo, tier="B")
        cmp = prov.compare_footprint(before, after)
        self.assertEqual(cmp["verdict"], "clean", cmp["violations"])


# ---------------------------------------------------------------------------
@unittest.skipIf(prov is None, SKIP)
@unittest.skipUnless(TESTBED.exists(), f"sealed testbed not present at {TESTBED}")
class AgainstARealRepository(unittest.TestCase, FourProbeMixin):
    """Item 6 asks for the re-proof to run "against a real repository".
    This is the sealed testbed A testbed -- a real checkout with real
    history, not a fixture built three functions above. Read-only by
    construction: the only entry points called are provenance's own
    read path."""

    # Probe 4 (the `find -newer` mtime sweep) is deliberately NOT asserted
    # in this class, and the reason is measured rather than assumed. The
    # testbed is a SHARED directory: other suites in this repository
    # (`test_zero_footprint`, `test_publication_footprint.
    # BorrowedTestbedReadOnly`) read the same checkout, and a concurrent
    # reader bumps `.git`'s own directory mtime. A no-operation control --
    # create marker, sleep 2s, sweep, with nothing at all running in
    # between -- reported
    #   ['/home/operator/testbeds/.nightshift-testbed/testbed A/.git']
    # on 1 of 4 windows while other suites were running. An mtime assertion
    # on a shared directory measures the neighbours, not the code. Probes
    # 1-3 are content-level (mtime is not part of probe 3's entries) and
    # stay valid under concurrent READERS, which is all the other suites
    # are. The strict probe-4 sweep runs in
    # DefaultModeWritesNothingIntoTheCustomerRepository instead, against a
    # repository this test exclusively owns.
    def test_the_read_only_provenance_pipeline_leaves_the_testbed_untouched(self):
        repo = str(TESTBED)
        roots = prov.resolve_customer_roots(repo)
        git_dir = roots["gitdir"]
        before = snapshot(repo, git_dir)

        prov.repository_fingerprint(repo)
        prov.collect_git_views(repo)
        prov.capture_footprint(repo, tier="A")
        prov.discover_closure(repo)
        prov.detect_dirty(repo)

        after = snapshot(repo, git_dir)
        self.assert_no_footprint(before, after, msg="testbed: ")

    def test_the_write_choke_point_refuses_every_write_verb_on_the_real_testbed(self):
        """The guard that makes the above true by construction rather than
        by luck, exercised against the real repository."""
        for argv in (["commit", "-m", "x"], ["push"], ["checkout", "main"],
                     ["worktree", "add", "/tmp/x"], ["stash", "push"],
                     ["gc"], ["fetch"], ["update-ref", "refs/heads/x", "HEAD"],
                     ["-c", "core.hooksPath=/tmp", "status"],
                     ["--git-dir=/tmp/elsewhere", "status"]):
            with self.subTest(argv=argv):
                with self.assertRaises(prov.ProvenanceError) as ctx:
                    prov.customer_git(str(TESTBED), argv)
                self.assertEqual(ctx.exception.reason_code, "customer_repo_write_refused")



# ---------------------------------------------------------------------------
# #695: a module that EXISTS but fails to import is a FAILURE, never a skip.
#
# The loader above returns None on any import error so the suite reports a
# reason at run time instead of erroring at collection.  That is right for a
# genuinely ABSENT module and wrong for a broken one: a syntax error in the
# module under test made every test here skip and the run report
# "OK (skipped=N)" -- CI green on code that cannot be imported.  Proven by
# appending invalid syntax to the module and watching this file report
# `Ran 25 tests ... OK (skipped=25)`.
#
# This guard restores the signal without reintroducing a collection error:
# absent module -> still skips, broken module -> one loud failure carrying
# the traceback.
# ---------------------------------------------------------------------------

class ModuleUnderTestImports(unittest.TestCase):

    def test_a_module_that_exists_must_import(self):
        if _IMPORT_ERROR is not None:
            self.fail(
                "the module under test exists but failed to import; every "
                "other test in this file skipped rather than ran:\n\n"
                + _IMPORT_ERROR)

if __name__ == "__main__":
    unittest.main()
