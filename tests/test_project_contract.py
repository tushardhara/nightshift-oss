"""Issue #443 -- Project Contract v1, schema v2 (PLAN-400-memory-system.md Track B).

Schema v2 replaces v1's ad-hoc {"cmd","confidence","evidence"} shapes with
one envelope (status/value/confidence/evidence/reason/unverified/candidates/
rejected) used identically by all 16 fields, closes the seven acceptance
criteria measured unmet on 2026-09-04:

  AC-1  exact-SHA discovery, refuse (never fall back) on a SHA not present.
  AC-2  16 attempted fields (languages, runtime_version,
        dependency_manifests, lockfiles, build, test, integration, e2e,
        lint, format, typecheck, codegen, migrations, ci, ownership,
        excluded_dirs).
  AC-3  confidence is the "observed"|"declared" enum, never a float; a
        populated field always carries non-empty evidence.
  AC-4  an unobserved field is `status: "absent"` with a non-empty reason.
  AC-5  two disagreeing sources mark the field `status: "conflict"`,
        `value: null` -- never a silently picked winner.
  AC-6  `baseline_sha` + `contract_digest` (sha256 over the contract's own
        canonical form).
  AC-7  byte-identical re-runs at the same SHA, asserted across a real
        subprocess boundary (PYTHONHASHSEED varied), not assumed.

AC-8 (never crashes on an unfamiliar layout) and AC-9 (writes nothing into
the target repository) were already met and are re-verified here rather
than re-designed.

INTERFACE (`bin/nightshift_project_contract.py`)
--------------------------------------------------
    build_contract(repo_path: str, *, sha: str) -> dict   # keyword-only sha, no default

`repo_path` must be the git repository's own toplevel; discovery reads
`git ls-tree`/`git cat-file --batch` at the given `sha` -- never the working
tree, the index, or HEAD (PLAN-400 sec 1 + the issue's #103 defect shape).
Every fixture in this suite, synthetic ones included, is a real (if
minimal) git repo with one commit -- see `_write_fixture`.

Full field-by-field shape: `bin/nightshift_project_contract.py`'s module
docstring and its `CONTRACT_FIELDS`/`validate_contract`/schema comments.
This suite exercises it, not repeats it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import traceback
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MODULE_PATH = BIN / "nightshift_project_contract.py"

# The borrowed-project testbeds (PLAN-400 sec 1): real, deliberately
# un-Nightshift-like repos. Not this suite's to create or seed -- skip
# cleanly per-class if the machine running the suite doesn't have them.
# Both are read-only from this suite's point of view: build_contract only
# runs `git rev-parse`/`ls-tree`/`cat-file`, never a write (re-verified by
# `NoWriteToTargetRepo` below, not just assumed).
TESTBED_A = Path("/home/operator/testbeds/.nightshift-testbed/testbed A")
TESTBED_B = Path("/home/operator/testbeds/.nightshift-testbed/testbed B")


_IMPORT_ERROR = None

def _load_contract_module():
    """Import bin/nightshift_project_contract.py by path.

    Returns None (never raises) both when the module doesn't exist yet AND
    when it exists but fails to import, so the suite can skip with a
    reason instead of erroring at collection either way.
    """
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        "nightshift_project_contract", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 -- see docstring: "not ready" too
        global _IMPORT_ERROR
        _IMPORT_ERROR = traceback.format_exc()
        print(f"WARNING: {MODULE_PATH} failed to import: {exc}", file=sys.stderr)
        return None
    finally:
        sys.path.remove(str(BIN))
    return module


CONTRACT = _load_contract_module()
SKIP_REASON = (
    f"{MODULE_PATH} not implemented (or not currently importable) -- #443, "
    "PLAN-400 Track B. This suite is written against the interface documented "
    "in this file's module docstring.")


def _head_sha(repo: Path) -> str:
    """Resolve HEAD OUTSIDE the library, exactly the division of labour
    AC-1 requires: a caller that wants HEAD runs `git rev-parse HEAD`
    itself and passes the result to `build_contract`; the library never
    performs that resolution.
    """
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True)
    return out.stdout.strip()


def detect(root, sha=None):
    """Call the module's public entry point; assert the JSON-shape contract."""
    if sha is None:
        sha = _head_sha(Path(root))
    contract = CONTRACT.build_contract(str(root), sha=sha)
    # The whole dict must round-trip through json.dumps -- a project_contract
    # that seats read and that becomes a memory-DB row must be plain data.
    round_tripped = json.loads(json.dumps(contract))
    assert round_tripped == contract, "contract does not round-trip through json.dumps"
    return contract


def _lang(contract, name):
    for entry in contract["languages"]["value"]["all"]:
        if entry.get("language") == name:
            return entry
    return None


def _excluded_entry(contract, prefix):
    block = contract["excluded_dirs"]
    if block["status"] != "observed":
        return None
    for entry in block["value"]["dirs"]:
        if str(entry.get("path", "")).strip("/").startswith(prefix):
            return entry
    return None


def _write_fixture(files):
    """Materialise `files` (relative path -> text content) as a real,
    one-commit git repo under a fresh tmpdir. Returns (root, sha).

    A plain directory is not enough: discovery reads `git ls-tree`/
    `cat-file`, on purpose. `user.email`/`user.name` are set LOCAL to the
    fixture repo only -- never `--global`.

    Cleanup is the caller's `addCleanup`, kept explicit at the call site.
    """
    root = tempfile.mkdtemp(prefix="nightshift-contract-fixture-")
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "fixture@example.com"],
        ["git", "config", "user.name", "Nightshift Contract Fixture"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "fixture"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=True).stdout.strip()
    return root, sha


# ===========================================================================
# AC-1 -- exact SHA, refuse rather than fall back
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class BatchReadsSurviveHostilePathNames(unittest.TestCase):
    """Audit low: `TreeAtSha.read` wrote `<sha>:<path>\\n` to one long-lived
    `cat-file --batch`. A tracked path holding a newline split into two
    queries and desynchronised every later read; a non-UTF-8 path raised.
    The tree is built with `mktree -z`, so no filesystem has to accept the
    names."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nightshift-contract-paths-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        def git(*args, data=None):
            return subprocess.run(["git", "-C", self.root, *args], input=data,
                                  capture_output=True, check=True).stdout

        git("init", "-q")
        entries = []
        for name, body in ((b"a\nb", b"newline\n"), (b"c\r", b"cr\n"),
                           (b"\xff.txt", b"latin\n"), (b"z.txt", b"zed\n")):
            blob = git("hash-object", "-w", "--stdin", data=body).strip()
            entries.append(b"100644 blob " + blob + b"\t" + name + b"\0")
        tree = git("mktree", "-z", data=b"".join(entries)).strip().decode()
        self.sha = git("-c", "user.name=f", "-c", "user.email=f@example.com",
                       "commit-tree", tree, "-m", "paths").strip().decode()

    def test_unsafe_names_are_skipped_and_later_reads_stay_in_sync(self):
        with CONTRACT.open_tree(self.root, self.sha) as tree:
            self.assertIn("a\nb", tree.paths)
            self.assertEqual(tree.read("z.txt"), b"zed\n")
            self.assertIsNone(tree.read("a\nb"))
            self.assertIsNone(tree.read("c\r"))
            self.assertEqual(tree.read(b"\xff.txt".decode(
                "utf-8", "surrogateescape")), b"latin\n")
            self.assertEqual(tree.read("z.txt"), b"zed\n")


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class ShaRefusal(unittest.TestCase):
    """The #103 defect shape named directly in the issue: `--ref` silently
    ignored, ambient checkout stamped instead. Every one of these must
    raise ShaNotPresent (never silently resolve to HEAD or the working
    tree) -- verified by calling build_contract with a bad sha, not by
    reading the source and assuming.
    """

    def setUp(self):
        self.root, self.sha = _write_fixture({
            "go.mod": "module fixture/sharefusal\n\ngo 1.21\n",
            "main.go": "package main\n\nfunc main() {}\n",
        })
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_head_literal_is_refused(self):
        """The exact #103 shape: passing the string "HEAD" must refuse,
        not resolve.
        """
        with self.assertRaises(CONTRACT.ShaNotPresent) as ctx:
            CONTRACT.build_contract(self.root, sha="HEAD")
        self.assertIn("HEAD", str(ctx.exception))

    def test_abbreviated_sha_is_refused(self):
        abbrev = self.sha[:7]
        with self.assertRaises(CONTRACT.ShaNotPresent) as ctx:
            CONTRACT.build_contract(self.root, sha=abbrev)
        self.assertIn(abbrev, str(ctx.exception))

    def test_uppercase_sha_is_refused(self):
        with self.assertRaises(CONTRACT.ShaNotPresent):
            CONTRACT.build_contract(self.root, sha=self.sha.upper())

    def test_branch_name_is_refused(self):
        with self.assertRaises(CONTRACT.ShaNotPresent):
            CONTRACT.build_contract(self.root, sha="main")

    def test_well_formed_but_absent_sha_is_refused_and_named(self):
        fake_sha = ("a" * 39) + "1"
        self.assertNotEqual(fake_sha, self.sha)
        with self.assertRaises(CONTRACT.ShaNotPresent) as ctx:
            CONTRACT.build_contract(self.root, sha=fake_sha)
        self.assertIn(fake_sha, str(ctx.exception))

    def test_sha_from_a_different_repository_is_refused(self):
        """A real SHA -- just not one that exists IN THIS repo. The
        overwhelmingly likely real-world way #103 bites: a caller passes a
        sha it read from the wrong place.
        """
        other_root, other_sha = _write_fixture({"README.md": "other repo\n"})
        self.addCleanup(shutil.rmtree, other_root, ignore_errors=True)
        self.assertNotEqual(other_sha, self.sha)
        with self.assertRaises(CONTRACT.ShaNotPresent) as ctx:
            CONTRACT.build_contract(self.root, sha=other_sha)
        self.assertIn(other_sha, str(ctx.exception))

    def test_repo_path_must_be_the_toplevel_not_a_subdirectory(self):
        """From a subdirectory, `ls-tree` without --full-tree would list
        only that subdirectory's paths, silently -- a correctness bug and
        an evidence-path bug. Refused as a usage error (NotADirectoryError,
        CLI exit 2), distinct from ShaNotPresent (CLI exit 3): the SHA is
        fine, the repo_path argument is what's wrong.
        """
        sub = os.path.join(self.root, "nonexistent-subdir")
        os.makedirs(sub, exist_ok=True)
        with self.assertRaises(NotADirectoryError):
            CONTRACT.build_contract(sub, sha=self.sha)

    def test_nonexistent_path_is_a_usage_error_not_a_sha_refusal(self):
        with self.assertRaises(NotADirectoryError):
            CONTRACT.build_contract("/no/such/path/at/all", sha=self.sha)

    def test_build_contract_sha_is_keyword_only_with_no_default(self):
        """A caller cannot accidentally omit the SHA and get a silent
        default -- TypeError, not a fallback to HEAD.
        """
        with self.assertRaises(TypeError):
            CONTRACT.build_contract(self.root)  # missing required kwarg
        with self.assertRaises(TypeError):
            CONTRACT.build_contract(self.root, self.sha)  # positional -- rejected

    def test_no_git_subprocess_argument_is_the_literal_string_HEAD(self):
        """Static guard: the literal quoted string "HEAD"/'HEAD' appears
        nowhere in the module as a usable value -- its one occurrence in
        the source is inside a docstring sentence describing this very
        property. A fallback to HEAD is therefore not just untested but
        structurally absent.
        """
        import re
        text = MODULE_PATH.read_text(encoding="utf-8")
        hits = [m.start() for m in re.finditer(r'["\']HEAD["\']', text)]
        self.assertLessEqual(len(hits), 1, "unexpected literal 'HEAD'/\"HEAD\" occurrence(s)")
        for token in ("git_head_sha", "git_ls_files", "os.walk(", ".is_file()",
                      "walk_files", "walk_sizes"):
            self.assertNotIn(token, text, f"v1 working-tree/index fallback surface "
                              f"{token!r} must not exist in v2")

    def test_cli_exits_3_on_sha_refusal_and_names_the_sha(self):
        fake_sha = ("b" * 39) + "2"
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), self.root, "--sha", fake_sha],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn(fake_sha, proc.stderr)

    def test_cli_exits_2_on_usage_error(self):
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "/no/such/path", "--sha", self.sha],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr)


# ===========================================================================
# AC-9 -- discovery writes nothing into the target repository
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class NoWriteToTargetRepo(unittest.TestCase):

    def test_git_status_and_head_unchanged_after_build_contract(self):
        root, sha = _write_fixture({
            "go.mod": "module fixture/nowrite\n\ngo 1.21\n",
            "main.go": "package main\n\nfunc main() {}\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        status_before = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                        capture_output=True, text=True, check=True).stdout
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                      capture_output=True, text=True, check=True).stdout

        detect(root, sha)
        detect(root, sha)  # twice, in case the first run's side effects only show up later

        status_after = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                       capture_output=True, text=True, check=True).stdout
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                     capture_output=True, text=True, check=True).stdout
        self.assertEqual(status_before, status_after)
        self.assertEqual(head_before, head_after)


# ===========================================================================
# AC-3 / AC-4 -- the envelope, structurally, on every field of every fixture
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class EnvelopeInvariants(unittest.TestCase):
    """`validate_contract()` is the AC-3 state table, enforced. It already
    runs inside `build_contract()` (an invalid contract would have raised
    before `detect()` got a return value at all) -- this class re-runs it
    explicitly against several independently-built fixtures to prove the
    public function works standalone, and checks a few properties
    `validate_contract` itself doesn't (field coverage, digest format).
    """

    def _check(self, contract, tree):
        CONTRACT.validate_contract(contract, tree)  # must not raise
        self.assertEqual(set(contract) - set(CONTRACT.CONTRACT_FIELDS),
                          {"schema_version", "baseline_sha", "contract_digest",
                           "local", "tree", "warnings"})
        self.assertEqual(contract["schema_version"], "2")
        self.assertTrue(contract["contract_digest"].startswith("sha256:"))
        self.assertEqual(len(contract["contract_digest"]), len("sha256:") + 64)
        for field in CONTRACT.CONTRACT_FIELDS:
            env = contract[field]
            self.assertIn(env["status"], ("observed", "declared", "conflict", "absent"))
            if env["status"] in ("observed", "declared"):
                self.assertIsInstance(env["value"], dict)
                self.assertTrue(env["evidence"])
                self.assertEqual(env["confidence"], env["status"])
                self.assertGreaterEqual(len(env["candidates"]), 1)
                self.assertIsNone(env["reason"])
            elif env["status"] == "absent":
                self.assertIsNone(env["value"])
                self.assertIsNone(env["confidence"])
                self.assertEqual(env["evidence"], [])
                self.assertTrue((env["reason"] or "").strip(),
                                 f"{field}: absent with no reason is exactly the silent-"
                                 f"null AC-4 exists to forbid")
            elif env["status"] == "conflict":
                self.assertIsNone(env["value"])
                self.assertGreaterEqual(len(env["candidates"]), 2)
                self.assertTrue(env["evidence"])
                self.assertTrue((env["reason"] or "").strip())

    def test_this_repo(self):
        sha = _head_sha(REPO)
        contract = detect(REPO, sha)
        with CONTRACT.open_tree(str(REPO), sha) as tree:
            self._check(contract, tree)

    def test_unfamiliar_layout_fixture(self):
        root, sha = _write_fixture({
            "README.md": "# fixture\n\nNo recognizable stack here.\n",
            "NOTES.txt": "just some prose\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        with CONTRACT.open_tree(root, sha) as tree:
            self._check(contract, tree)

    def test_polyglot_fixture(self):
        files = {
            "go.mod": "module fixture/ambiguous\n\ngo 1.21\n",
            "pyproject.toml": "[project]\nname = \"fixture\"\nversion = \"0.1.0\"\n",
        }
        for i in range(6):
            files[f"pkg/mod{i}.go"] = f"package pkg\nfunc F{i}() int {{ return {i} }}\n"
            files[f"pkg/mod{i}.py"] = f"def f{i}():\n    return {i}\n"
        root, sha = _write_fixture(files)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        with CONTRACT.open_tree(root, sha) as tree:
            self._check(contract, tree)

    def test_a_hand_corrupted_contract_fails_validation(self):
        """AC-3's own words: "a value with no evidence is invalid and
        fails the run." Constructed directly against `validate_contract`,
        not inferred from `build_contract` never producing one.
        """
        root, sha = _write_fixture({
            "go.mod": "module fixture/corrupt\n\ngo 1.21\n",
            "main.go": "package main\n\nfunc main() {}\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        with CONTRACT.open_tree(root, sha) as tree:
            broken = json.loads(json.dumps(contract))
            broken["build"]["evidence"] = []  # status stays "observed"/"declared"
            with self.assertRaises(CONTRACT.ContractInvalid):
                CONTRACT.validate_contract(broken, tree)

    def test_a_hand_corrupted_confidence_float_fails_validation(self):
        """v1's exact defect shape (a float confidence) must be rejected,
        not silently accepted because *some* number is there.
        """
        root, sha = _write_fixture({
            "go.mod": "module fixture/corruptfloat\n\ngo 1.21\n",
            "main.go": "package main\n\nfunc main() {}\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        with CONTRACT.open_tree(root, sha) as tree:
            broken = json.loads(json.dumps(contract))
            broken["languages"]["confidence"] = 0.9
            with self.assertRaises(CONTRACT.ContractInvalid):
                CONTRACT.validate_contract(broken, tree)


# ===========================================================================
# AC-6 -- baseline_sha + contract_digest
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class BaselineShaAndDigest(unittest.TestCase):

    def setUp(self):
        self.root, self.sha = _write_fixture({
            "go.mod": "module fixture/digest\n\ngo 1.21\n",
            "main.go": "package main\n\nfunc main() {}\n",
        })
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.contract = detect(self.root, self.sha)

    def test_baseline_sha_is_the_exact_sha_passed_in(self):
        self.assertEqual(self.contract["baseline_sha"], self.sha)

    def test_digest_is_sha256_over_the_canonical_form(self):
        import hashlib
        recomputed = "sha256:" + hashlib.sha256(
            CONTRACT.canonical_bytes(self.contract)).hexdigest()
        self.assertEqual(self.contract["contract_digest"], recomputed)

    def test_digest_excludes_contract_digest_and_local(self):
        """AC-6: local (absolute machine path, git version) is environment,
        not contract -- digesting it would make the digest non-portable
        across clones of the identical tree. contract_digest obviously
        can't hash over itself.
        """
        self.assertEqual(CONTRACT.DIGEST_EXCLUDED_TOP_LEVEL_KEYS,
                          ("contract_digest", "local"))
        mutated = json.loads(json.dumps(self.contract))
        mutated["local"] = {"repo_path": "/completely/different/path", "git_version": "0.0.0"}
        mutated["contract_digest"] = "sha256:" + ("f" * 64)
        self.assertEqual(CONTRACT.canonical_bytes(mutated),
                          CONTRACT.canonical_bytes(self.contract))

    def test_digest_changes_when_a_real_field_changes(self):
        mutated = json.loads(json.dumps(self.contract))
        mutated["warnings"] = mutated["warnings"] + ["an extra warning that was not there"]
        self.assertNotEqual(CONTRACT.canonical_bytes(mutated),
                             CONTRACT.canonical_bytes(self.contract))


# ===========================================================================
# AC-7 -- determinism, across a real subprocess boundary
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class Determinism(unittest.TestCase):
    """An in-process double call cannot catch PYTHONHASHSEED-driven
    hazards (fixed for the life of one interpreter) -- this spawns the CLI
    as a real subprocess with varying PYTHONHASHSEED, per AC-7's own
    "asserted by a test, not assumed."
    """

    @classmethod
    def setUpClass(cls):
        cls.root, cls.sha = _write_fixture({
            "go.mod": "module fixture/determinism\n\ngo 1.21\n",
            "pyproject.toml": "[project]\nname = \"fixture\"\nversion = \"0.1.0\"\n",
            "main.go": "package main\n\nfunc main() {}\n",
            "pkg/a.go": "package pkg\nfunc A() int { return 1 }\n",
            "pkg/b.py": "def b():\n    return 2\n",
            ".github/workflows/ci.yml": (
                "name: ci\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: go test ./... -short\n"),
        })

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _run_cli(self, cwd, extra_env=None):
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), self.root, "--sha", self.sha, "--compact"],
            capture_output=True, cwd=cwd, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout

    def test_byte_identical_across_pythonhashseed(self):
        out_seed0 = self._run_cli(str(REPO), {"PYTHONHASHSEED": "0"})
        out_seed_other = self._run_cli(str(REPO), {"PYTHONHASHSEED": "12345"})
        out_seed_random = self._run_cli(str(REPO), {"PYTHONHASHSEED": "random"})
        self.assertEqual(out_seed0, out_seed_other)
        self.assertEqual(out_seed0, out_seed_random)

    def test_byte_identical_from_a_different_cwd(self):
        baseline = self._run_cli(str(REPO), {"PYTHONHASHSEED": "0"})
        with tempfile.TemporaryDirectory() as other_cwd:
            other = self._run_cli(other_cwd, {"PYTHONHASHSEED": "0"})
        self.assertEqual(baseline, other)

    def test_digest_identical_across_two_independent_clones(self):
        """The strongest AC-7 check: two SEPARATE clones of the same tree
        at the same SHA, at two different absolute paths, must produce the
        same contract_digest -- catching anything absolute leaking into
        the digested payload (the full stdout legitimately differs between
        clones only in the excluded `local.repo_path` field).
        """
        clone_dir = tempfile.mkdtemp(prefix="nightshift-contract-clone-")
        self.addCleanup(shutil.rmtree, clone_dir, ignore_errors=True)
        clone_path = os.path.join(clone_dir, "clone")
        subprocess.run(["git", "clone", "-q", self.root, clone_path],
                        check=True, capture_output=True)
        contract_a = detect(self.root, self.sha)
        contract_b = detect(clone_path, self.sha)
        self.assertEqual(contract_a["contract_digest"], contract_b["contract_digest"])
        self.assertNotEqual(contract_a["local"]["repo_path"], contract_b["local"]["repo_path"])
        payload_a = {k: v for k, v in contract_a.items()
                     if k not in CONTRACT.DIGEST_EXCLUDED_TOP_LEVEL_KEYS}
        payload_b = {k: v for k, v in contract_b.items()
                     if k not in CONTRACT.DIGEST_EXCLUDED_TOP_LEVEL_KEYS}
        self.assertEqual(payload_a, payload_b)


# ===========================================================================
# Real borrowed-project testbeds -- Go/testbed A, Python/Nightshift,
# Java/testbed B. Adapted to the v2 envelope shape; every assertion re-verified
# by RUNNING against a real, currently-built build_contract().
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class GoDetectionOnTestbedA(unittest.TestCase):
    """The borrowed-project case PLAN-400 sec 1 calls "the whole product":
    Nightshift-on-a-customer-repo must not fall back to its own baked-in
    Python/pytest assumptions.
    """

    @classmethod
    def setUpClass(cls):
        if not TESTBED_A.exists():
            raise unittest.SkipTest(
                f"borrowed-project testbed not checked out at {TESTBED_A}")
        cls.sha = _head_sha(TESTBED_A)
        cls.contract = detect(TESTBED_A, cls.sha)

    def test_go_is_the_primary_language(self):
        value = self.contract["languages"]["value"]
        self.assertEqual(value["primary"], "go")
        go = _lang(self.contract, "go")
        self.assertIsNotNone(go, value["all"])
        self.assertGreater(go["share_ppm"], 500_000)
        self.assertFalse(value["ambiguous"])

    def test_python_is_present_but_outranked(self):
        """25 .py files exist with no requirements.txt/pyproject.toml at
        root -- evidence-ranked detection must not let raw file count beat
        `go.mod` + 329 `.go`. Python IS expected to show up as a candidate
        (real evidence), just never ahead of go.
        """
        go = _lang(self.contract, "go")
        py = _lang(self.contract, "python")
        self.assertIsNotNone(py)
        self.assertLess(py["share_ppm"], go["share_ppm"])

    def test_not_pytest(self):
        test = self.contract["test"]["value"]
        self.assertNotEqual(test["framework"], "pytest")
        self.assertNotIn("pytest", test["command"].lower())

    def test_real_test_command_is_go_test_ci_observed(self):
        """`.github/workflows/testbed A-pr-checks.yml` runs `go test
        ./... -short` -- the real, CI-sourced command, not an assumed one.
        """
        env = self.contract["test"]
        self.assertIn("go test", env["value"]["command"])
        self.assertEqual(env["status"], "observed")
        self.assertEqual(env["confidence"], "observed")
        winner = env["candidates"][0]
        self.assertEqual(winner["source_kind"], "ci_observed")
        self.assertTrue(env["evidence"])
        self.assertTrue(any("testbed A-pr-checks.yml" in p for p in env["evidence"]))

    def test_generated_dirs_report_protos_with_a_reason(self):
        entry = _excluded_entry(self.contract, "protos")
        self.assertIsNotNone(entry, self.contract["excluded_dirs"])
        self.assertTrue((entry.get("reason") or "").strip())
        self.assertTrue((entry.get("note") or "").strip())
        self.assertTrue(entry.get("evidence"))

    def test_gitignored_graphify_out_is_reported_with_a_reason(self):
        entry = _excluded_entry(self.contract, "graphify-out")
        self.assertIsNotNone(entry, self.contract["excluded_dirs"])
        self.assertTrue((entry.get("reason") or "").strip())

    def test_discovery_is_bounded_by_tracked_content_not_full_disk(self):
        started = time.monotonic()
        detect(TESTBED_A, self.sha)
        self.assertLess(time.monotonic() - started, 20.0)

    def test_no_git_internals_leak_into_the_contract(self):
        blob = json.dumps(self.contract)
        self.assertNotIn("/.git/", blob)

    def test_every_evidence_path_is_repo_relative_and_real(self):
        """Every evidence path anywhere in the contract exists as a
        tracked path at baseline_sha -- the exact thing validate_contract
        enforces, re-checked here directly against a fresh tree open.
        """
        with CONTRACT.open_tree(str(TESTBED_A), self.sha) as tree:
            CONTRACT.validate_contract(self.contract, tree)


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class PythonDetectionOnNightshift(unittest.TestCase):
    """Dogfood A: detection against Nightshift's own repo. 93+ `.py` files,
    no setup.py/pyproject.toml/requirements.txt at root, and a real suite
    (`tests/run.sh` -> `tests/run_suite.py`) that imports stdlib
    `unittest`, never `pytest`.
    """

    @classmethod
    def setUpClass(cls):
        cls.sha = _head_sha(REPO)
        cls.contract = detect(REPO, cls.sha)

    def test_python_is_the_primary_language(self):
        self.assertEqual(self.contract["languages"]["value"]["primary"], "python")

    def test_fixture_languages_do_not_leak_into_this_repos_contract(self):
        """The regression that shipped in #693 and went red in CI.

        `tests/fixtures/dogfood_b_java/` is a real, tracked Java + Cypress
        project -- deliberately, it is what #443's rejection test is run
        against. It describes a FIXTURE, not this repository. Before the
        detection-scope filter existed, committing it made Nightshift report
        itself as a java/typescript polyglot with maven and npm manifests.

        `test_python_is_the_primary_language` did not catch it: python was
        still primary at 98.3% of bytes, so asserting on `primary` alone
        passed while the language LIST was wrong.
        """
        langs = {entry["language"] for entry in self.contract["languages"]["value"]["all"]}
        self.assertEqual(langs, {"python"}, f"fixture languages leaked in: {sorted(langs)}")

    def test_no_field_cites_a_fixture_path_as_evidence(self):
        """The general form, so a future fixture in any language cannot
        silently redefine what this repository is."""
        leaked = {}
        for name, env in self.contract.items():
            if not isinstance(env, dict) or "evidence" not in env:
                continue
            bad = [e for e in (env.get("evidence") or [])
                   if isinstance(e, str) and "/fixtures/" in f"/{e}"]
            if bad:
                leaked[name] = bad
        self.assertEqual(leaked, {}, f"fields citing fixture evidence: {leaked}")

    def test_test_framework_is_unittest_not_pytest(self):
        env = self.contract["test"]
        self.assertEqual(env["value"]["framework"], "unittest")
        self.assertIn("tests/run.sh", env["value"]["command"])

    def test_test_command_is_confidently_evidenced_not_defaulted(self):
        env = self.contract["test"]
        self.assertIn(env["status"], ("observed", "declared"))
        self.assertIn(env["confidence"], ("observed", "declared"))
        self.assertTrue(env["evidence"])

    def test_tests_ci_check_is_observed(self):
        """The issue names this check explicitly: the contract "must
        observe ... the `tests` GitHub Actions check". `tests.yml`'s
        workflow-level `name:` is "control-plane tests" -- a different
        string -- and its one job (`tests:`) has no job-level `name:`, so
        the check GitHub Actions actually produces, and the one branch
        protection would key a required status check against, is the job
        id "tests". Asserting only CI-system membership (as this test used
        to) proves nothing about the check name itself.
        """
        ci = self.contract["ci"]["value"]
        names = {s["system"] for s in ci["systems"]}
        self.assertIn("github_actions", names)
        gha = next(s for s in ci["systems"] if s["system"] == "github_actions")
        self.assertIn("tests", gha["checks"])
        self.assertNotIn("control-plane tests", gha["checks"])

    def test_stdlib_only_no_python_dependency_manifest(self):
        """The gap Dogfood A exists to measure: the Python code has no
        dependency manifest. The one manifest in the tree is the npm lock
        that pins the harness CLIs the deploy installs, and it must stay the
        only one -- a requirements file or pyproject here means the
        stdlib-only rule broke.
        """
        env = self.contract["dependency_manifests"]
        self.assertEqual(env["status"], "observed")
        self.assertEqual(env["value"]["files"],
                         [{"path": "deploy/harness-clis/package.json", "ecosystem": "npm"}])


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class NodeFixtureDetection(unittest.TestCase):

    def setUp(self):
        self.root, self.sha = _write_fixture({
            "package.json": json.dumps({
                "name": "fixture-node", "version": "1.0.0",
                "scripts": {"test": "jest", "build": "webpack", "lint": "eslint ."},
                "devDependencies": {"jest": "^29.0.0"},
            }),
            "index.js": "module.exports = () => 1;\n",
            "src/app.js": "console.log('hi');\n",
            "test/app.test.js": "test('x', () => expect(1).toBe(1));\n",
        })
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.contract = detect(self.root, self.sha)

    def test_javascript_is_the_primary_language(self):
        self.assertEqual(self.contract["languages"]["value"]["primary"], "javascript")

    def test_test_framework_is_evidence_derived_not_pytest(self):
        framework = self.contract["test"]["value"]["framework"]
        self.assertNotEqual(framework, "pytest")
        self.assertEqual(framework, "jest")
        self.assertEqual(self.contract["test"]["candidates"][0]["source_kind"],
                          "manifest_script")


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class RustFixtureDetection(unittest.TestCase):

    def setUp(self):
        self.root, self.sha = _write_fixture({
            "Cargo.toml": ("[package]\nname = \"fixture\"\nversion = \"0.1.0\"\n"
                            "edition = \"2021\"\n"),
            "src/main.rs": "fn main() { println!(\"hi\"); }\n",
            "src/lib.rs": "pub fn add(a: i32, b: i32) -> i32 { a + b }\n",
            "tests/it.rs": "#[test]\nfn it_works() { assert_eq!(2 + 2, 4); }\n",
        })
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.contract = detect(self.root, self.sha)

    def test_rust_is_the_primary_language(self):
        self.assertEqual(self.contract["languages"]["value"]["primary"], "rust")

    def test_build_and_test_commands_are_cargo(self):
        self.assertIn("cargo", self.contract["build"]["value"]["command"])
        self.assertIn("cargo", self.contract["test"]["value"]["command"])
        self.assertNotEqual(self.contract["test"]["value"]["framework"], "pytest")


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class MavenFixtureDetection(unittest.TestCase):
    """Maven is a build tool, not a language: the primary language is java.
    This pom.xml declares NO plugins at all, unlike the real testbed B testbed
    below -- only "a pom.xml exists" to go on, so the only surviving
    candidate is the low-confidence manifest_convention tier.
    """

    def setUp(self):
        self.root, self.sha = _write_fixture({
            "pom.xml": ("<project><modelVersion>4.0.0</modelVersion>"
                        "<groupId>com.example</groupId><artifactId>fixture</artifactId>"
                        "<version>1.0</version></project>\n"),
            "src/main/java/com/example/App.java": (
                "package com.example;\npublic class App {\n"
                "    public static void main(String[] args) {}\n}\n"),
            "src/test/java/com/example/AppTest.java": (
                "package com.example;\npublic class AppTest {}\n"),
        })
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.contract = detect(self.root, self.sha)

    def test_java_is_the_primary_language(self):
        self.assertEqual(self.contract["languages"]["value"]["primary"], "java")

    def test_build_command_is_maven(self):
        self.assertIn("mvn", self.contract["build"]["value"]["command"])
        self.assertNotEqual(self.contract["test"]["value"]["framework"], "pytest")

    def test_synthetic_pom_with_no_plugins_only_earns_declared_convention(self):
        env = self.contract["test"]
        self.assertNotEqual(env["status"], "absent")
        self.assertEqual(env["status"], "declared")
        self.assertEqual(env["confidence"], "declared")
        self.assertEqual(env["candidates"][0]["source_kind"], "manifest_convention")


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class MavenDetectionOnTestbedB(unittest.TestCase):
    """The real Maven/Java borrowed-project testbed, the third stack
    PLAN-400 names alongside Go/testbed A and Python/Nightshift. Root
    pom.xml declares maven-surefire-plugin AND spotless-maven-plugin, and
    a CI workflow runs `./mvnw spotless:apply -T1C` -- a REAL AC-5
    conflict against the plugin's own `spotless:check` goal, verified to
    exist today, not synthesized.
    """

    @classmethod
    def setUpClass(cls):
        if not TESTBED_B.exists():
            raise unittest.SkipTest(
                f"borrowed-project testbed not checked out at {TESTBED_B}")
        cls.sha = _head_sha(TESTBED_B)
        cls.contract = detect(TESTBED_B, cls.sha)

    def test_java_is_the_primary_language(self):
        value = self.contract["languages"]["value"]
        self.assertEqual(value["primary"], "java")
        java = _lang(self.contract, "java")
        self.assertIsNotNone(java, value["all"])
        self.assertGreater(java["share_ppm"], 500_000)

    def test_real_test_command_is_found_not_absent(self):
        env = self.contract["test"]
        self.assertIn(env["status"], ("observed", "declared"))
        self.assertIn("mvn", env["value"]["command"])
        self.assertTrue(env["evidence"])

    def test_python_decoy_never_wins_the_test_command(self):
        """A `.semaphore/` task for an unrelated Python microservice folded
        into this monorepo contains a literal `pytest` invocation. The
        stack gate (this is a Java repo) must keep it from ever becoming
        the winning test command.
        """
        env = self.contract["test"]
        self.assertNotIn("pytest", env["value"]["command"].lower())
        self.assertNotEqual(env["value"]["framework"], "pytest")

    def test_format_is_a_real_recorded_conflict(self):
        """The real, currently-existing AC-5 case: CI observes
        `./mvnw spotless:apply -T1C`, the plugin declaration's own check
        goal is `./mvnw spotless:check` -- apply REWRITES the tree, check
        does not. No winner may be picked.
        """
        env = self.contract["format"]
        self.assertEqual(env["status"], "conflict")
        self.assertIsNone(env["value"])
        self.assertGreaterEqual(len(env["candidates"]), 2)
        values = {c["value"]["command"] for c in env["candidates"]}
        self.assertIn("./mvnw spotless:apply -T1C", values)
        self.assertIn("./mvnw spotless:check", values)
        self.assertIn("spotless", env["reason"].lower())

    def test_lint_is_absent_naming_the_untracked_plugin(self):
        """pmd-ruleset.xml is tracked at root, but no maven-pmd-plugin is
        declared in pom.xml -- absent, with a reason naming the file, not
        a guess that PMD runs.
        """
        env = self.contract["lint"]
        self.assertEqual(env["status"], "absent")
        self.assertIn("pmd-ruleset.xml", env["reason"])

    def test_discovery_is_bounded_by_tracked_content_not_full_disk(self):
        started = time.monotonic()
        detect(TESTBED_B, self.sha)
        self.assertLess(time.monotonic() - started, 60.0)


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class AmbiguousPolyglotFixture(unittest.TestCase):
    """Deliberately tied evidence: a root manifest AND an equal file count
    on BOTH go and python. Must report ambiguity, not pick a side silently.
    """

    def setUp(self):
        files = {
            "go.mod": "module fixture/ambiguous\n\ngo 1.21\n",
            "pyproject.toml": "[project]\nname = \"fixture\"\nversion = \"0.1.0\"\n",
        }
        for i in range(6):
            files[f"pkg/mod{i}.go"] = f"package pkg\nfunc F{i}() int {{ return {i} }}\n"
            files[f"pkg/mod{i}.py"] = f"def f{i}():\n    return {i}\n"
        self.root, self.sha = _write_fixture(files)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.contract = detect(self.root, self.sha)

    def test_ambiguous_flag_is_set(self):
        self.assertTrue(self.contract["languages"]["value"]["ambiguous"])

    def test_both_tied_languages_are_visible_as_candidates(self):
        all_entries = self.contract["languages"]["value"]["all"]
        names = {c["language"] for c in all_entries}
        self.assertIn("go", names)
        self.assertIn("python", names)
        self.assertGreaterEqual(len(all_entries), 2)


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class UnknownStackFixture(unittest.TestCase):
    """No manifest, no recognizable source extension, nothing to rank.
    AC-8: never crashes, exits 0, mostly-null contract with reasons.
    """

    def setUp(self):
        self.root, self.sha = _write_fixture({
            "README.md": "# fixture\n\nNo recognizable stack here.\n",
            "NOTES.txt": "just some prose\n",
        })
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.contract = detect(self.root, self.sha)

    def test_languages_absent_not_defaulted(self):
        env = self.contract["languages"]
        self.assertEqual(env["status"], "absent")
        self.assertIsNone(env["value"])
        self.assertTrue((env["reason"] or "").strip())

    def test_no_command_defaults_to_pytest_or_anything_else(self):
        for category in ("test", "build", "lint"):
            env = self.contract[category]
            self.assertEqual(env["status"], "absent")
            self.assertIsNone(env["value"])
            self.assertTrue((env["reason"] or "").strip())

    def test_cli_exits_0_on_an_unfamiliar_repo(self):
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), self.root, "--sha", self.sha],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class GeneratedDirsExcludedWithReason(unittest.TestCase):

    def setUp(self):
        self.root, self.sha = _write_fixture({
            "go.mod": "module fixture/gen\n\ngo 1.21\n",
            "main.go": "package main\n\nfunc main() {}\n",
            "graphify-out/nodes.go": (
                "// Code generated by graphify. DO NOT EDIT.\npackage graphifyout\n"),
            "graphify-out/edges.go": (
                "// Code generated by graphify. DO NOT EDIT.\npackage graphifyout\n"),
            "protos/service.proto": "syntax = \"proto3\";\npackage fixture;\n",
            "protos/service.pb.go": (
                "// Code generated by protoc-gen-go. DO NOT EDIT.\npackage protos\n"),
        })
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.contract = detect(self.root, self.sha)

    def test_graphify_out_is_excluded_with_a_reason(self):
        entry = _excluded_entry(self.contract, "graphify-out")
        self.assertIsNotNone(entry, self.contract["excluded_dirs"])
        self.assertTrue((entry.get("reason") or "").strip())
        self.assertTrue(entry.get("evidence"))

    def test_protos_is_excluded_with_a_reason(self):
        entry = _excluded_entry(self.contract, "protos")
        self.assertIsNotNone(entry, self.contract["excluded_dirs"])
        self.assertTrue((entry.get("reason") or "").strip())


# ===========================================================================
# AC-5 -- a synthetic, deterministic conflict (independent of the real
# testbed's mutable state -- MavenDetectionOnTestbedB above proves the real
# one still exists today; this proves the RULE, on a fixture this suite
# controls).
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class ConflictingSourcesAreRecordedNotResolved(unittest.TestCase):

    def setUp(self):
        self.root, self.sha = _write_fixture({
            "pom.xml": (
                "<project><modelVersion>4.0.0</modelVersion>"
                "<groupId>com.example</groupId><artifactId>fixture</artifactId>"
                "<version>1.0</version>"
                "<build><plugins><plugin>"
                "<groupId>com.diffplug.spotless</groupId>"
                "<artifactId>spotless-maven-plugin</artifactId>"
                "</plugin></plugins></build></project>\n"),
            "mvnw": "#!/bin/sh\necho fixture mvnw\n",
            "src/main/java/com/example/App.java": (
                "package com.example;\npublic class App {}\n"),
            ".github/workflows/ci.yml": (
                "name: ci\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - name: format\n        run: ./mvnw spotless:apply\n"),
        })
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.contract = detect(self.root, self.sha)

    def test_format_field_is_marked_conflict_not_resolved(self):
        env = self.contract["format"]
        self.assertEqual(env["status"], "conflict")
        self.assertIsNone(env["value"], "the tool must never silently pick a winner")

    def test_both_candidates_are_recorded_with_their_own_evidence(self):
        env = self.contract["format"]
        self.assertGreaterEqual(len(env["candidates"]), 2)
        values = {c["value"]["command"] for c in env["candidates"]}
        self.assertIn("./mvnw spotless:apply", values)
        self.assertIn("./mvnw spotless:check", values)
        for c in env["candidates"]:
            self.assertTrue(c["evidence"])
        source_kinds = {c["source_kind"] for c in env["candidates"]}
        self.assertIn("ci_observed", source_kinds)
        self.assertIn("manifest_plugin", source_kinds)

    def test_conflict_evidence_is_the_union_and_reason_names_both(self):
        env = self.contract["format"]
        self.assertIn(".github/workflows/ci.yml", env["evidence"])
        self.assertIn("pom.xml", env["evidence"])
        self.assertIn("spotless:apply", env["reason"])
        self.assertIn("spotless:check", env["reason"])

    def test_a_field_with_only_one_distinct_value_is_not_a_conflict(self):
        """Sanity check on the rule itself: `test` here has only the
        manifest_convention candidate (no CI test step, no surefire
        plugin) -- one distinct value, never conflict.
        """
        env = self.contract["test"]
        self.assertNotEqual(env["status"], "conflict")


# ===========================================================================
# Dogfood B -- "the rejection matters more than the success" (the issue's
# own words). A deliberately boring Java/Maven/Spring Boot/JUnit/Cypress
# fixture, observed with evidence, then a claim proposing Go and Playwright
# is checked against the contract's own evidence and must be refused.
# ===========================================================================

def _dogfood_b_fixture_files():
    java_class_tmpl = (
        "package com.example.svc;\n\n"
        "public class Service{n} {{\n"
        "    public int compute(int x) {{ return x + {n}; }}\n"
        "}}\n"
    )
    files = {
        "pom.xml": (
            "<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n"
            "  <modelVersion>4.0.0</modelVersion>\n"
            "  <parent>\n"
            "    <groupId>org.springframework.boot</groupId>\n"
            "    <artifactId>spring-boot-starter-parent</artifactId>\n"
            "    <version>3.2.0</version>\n"
            "  </parent>\n"
            "  <groupId>com.example</groupId>\n"
            "  <artifactId>dogfoodb</artifactId>\n"
            "  <version>1.0.0</version>\n"
            "  <properties><maven.compiler.release>17</maven.compiler.release></properties>\n"
            "  <dependencies>\n"
            "    <dependency>\n"
            "      <groupId>org.springframework.boot</groupId>\n"
            "      <artifactId>spring-boot-starter-web</artifactId>\n"
            "    </dependency>\n"
            "    <dependency>\n"
            "      <groupId>org.springframework.boot</groupId>\n"
            "      <artifactId>spring-boot-starter-test</artifactId>\n"
            "      <scope>test</scope>\n"
            "    </dependency>\n"
            "    <dependency>\n"
            "      <groupId>org.junit.jupiter</groupId>\n"
            "      <artifactId>junit-jupiter-engine</artifactId>\n"
            "      <scope>test</scope>\n"
            "    </dependency>\n"
            "  </dependencies>\n"
            "  <build><plugins>\n"
            "    <plugin>\n"
            "      <groupId>org.springframework.boot</groupId>\n"
            "      <artifactId>spring-boot-maven-plugin</artifactId>\n"
            "    </plugin>\n"
            "    <plugin>\n"
            "      <groupId>org.apache.maven.plugins</groupId>\n"
            "      <artifactId>maven-surefire-plugin</artifactId>\n"
            "    </plugin>\n"
            "  </plugins></build>\n"
            "</project>\n"),
        "mvnw": "#!/bin/sh\necho fixture mvnw\n",
        "src/main/java/com/example/App.java": (
            "package com.example;\n"
            "public class App { public static void main(String[] a) {} }\n"),
        "src/test/java/com/example/AppTest.java": (
            "package com.example;\n"
            "import org.junit.jupiter.api.Test;\n"
            "class AppTest { @Test void ok() {} }\n"),
        "cypress.config.js": "module.exports = { e2e: { baseUrl: 'http://localhost:8080' } };\n",
        "cypress/e2e/smoke.cy.js": (
            "describe('smoke', () => { it('loads', () => { cy.visit('/'); }); });\n"),
        # Deliberately NOT at repo root -- mirrors the real testbed A
        # testbed's itf/package.json layout, and keeps a javascript root
        # manifest from tying against pom.xml in language scoring.
        "e2e/package.json": json.dumps({
            "name": "dogfoodb-e2e", "version": "1.0.0",
            "devDependencies": {"cypress": "^13.0.0"},
        }),
        ".github/workflows/ci.yml": (
            "name: dogfoodb CI\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@v4\n"
            "      - run: ./mvnw test\n      - run: npx cypress run\n"),
    }
    # A real Spring Boot service is not two files -- bulk out the Java side
    # so the fixture is unambiguously java-primary rather than a coin-flip
    # tie against its own small Cypress suite.
    for n in range(1, 21):
        files[f"src/main/java/com/example/svc/Service{n}.java"] = java_class_tmpl.format(n=n)
    return files


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class DogfoodBBoringForeignStack(unittest.TestCase):
    """"A demo where everything happens to go right proves nothing. A
    recorded refusal proves the contract is load-bearing." -- the issue,
    verbatim. This class first proves the five-signal observation
    (languages/build/test/e2e all carry real evidence, Spring Boot noted),
    then writes the refusal assertion the issue calls out as the one that
    counts: the contract's own evidence must contradict a claim proposing
    Go and Playwright.
    """

    @classmethod
    def setUpClass(cls):
        cls.root, cls.sha = _write_fixture(_dogfood_b_fixture_files())
        cls.contract = detect(cls.root, cls.sha)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_java_is_observed_as_the_unambiguous_primary_language(self):
        value = self.contract["languages"]["value"]
        self.assertEqual(value["primary"], "java")
        self.assertFalse(value["ambiguous"])
        self.assertTrue(self.contract["languages"]["evidence"])

    def test_maven_build_is_observed_with_evidence(self):
        env = self.contract["build"]
        self.assertIn(env["status"], ("observed", "declared"))
        self.assertIn("mvn", env["value"]["command"])
        self.assertIn("pom.xml", env["evidence"])

    def test_junit_test_framework_is_observed_with_evidence(self):
        env = self.contract["test"]
        self.assertIn(env["status"], ("observed", "declared"))
        self.assertEqual(env["value"]["framework"], "junit")
        self.assertIn("pom.xml", env["evidence"])

    def test_spring_boot_is_observed_in_the_evidence_trail(self):
        """No dedicated top-level field -- Spring Boot is a framework atop
        Maven/Java, not a language or a command -- but it must still be a
        real, cited fact in the contract's evidence trail, not silently
        dropped.
        """
        notes = " ".join(n for c in self.contract["test"]["candidates"] for n in c["notes"])
        self.assertIn("spring boot", notes.lower())
        self.assertIn("pom.xml", notes)

    def test_cypress_e2e_is_observed_with_evidence(self):
        env = self.contract["e2e"]
        self.assertEqual(env["status"], "observed")
        self.assertEqual(env["value"]["framework"], "cypress")
        # BOTH independent observations must survive in the trail.
        #
        # This assertion used to be `assertIn("cypress.config.js",
        # env["evidence"])`, which held only while the config file was the
        # ONLY thing seen. The CI extractor could not read `- run: npx cypress
        # run` (a step whose sequence dash sits on the `run:` line), so the
        # workflow was invisible. Now that it is read, the CI candidate wins
        # the envelope because it carries the actual COMMAND -- which is the
        # whole point for a seat resolving its toolchain -- and top-level
        # `evidence` names the workflow.
        #
        # The config file is NOT dropped: it remains a corroborating candidate
        # with the same value. Asserting across the whole trail is strictly
        # stronger than the old form -- it would fail if EITHER observation
        # disappeared, where the old one only watched the config file.
        trail = set(env["evidence"])
        for cand in env.get("candidates", ()):
            trail.update(cand.get("evidence", ()))
        self.assertIn("cypress.config.js", trail,
                      f"config-file observation dropped from the trail: {trail}")
        self.assertIn(".github/workflows/ci.yml", trail,
                      f"CI observation dropped from the trail: {trail}")
        self.assertEqual(env["value"]["command"], "npx cypress run",
                         "the winning candidate must carry the runnable "
                         "command, not just the framework name")

    def test_true_claim_is_not_falsely_refused(self):
        """Sanity check on `contradicts()` itself: an accurate claim must
        not be flagged.
        """
        true_claim = {"languages": {"primary": "java"}, "e2e": {"framework": "cypress"}}
        self.assertEqual(CONTRACT.contradicts(self.contract, true_claim), [])

    def test_the_refusal_that_matters_go_and_playwright_claim_is_contradicted(self):
        """THE assertion the issue names as the one that counts. #443 ships
        no enforcement gate yet (explicitly out of scope) -- this is an
        assertion over the contract's own contents, written now, so a
        later conformance-gate child inherits `contradicts()` directly
        rather than re-deriving the comparison.
        """
        claim = {"languages": {"primary": "go"}, "e2e": {"framework": "playwright"}}
        reasons = CONTRACT.contradicts(self.contract, claim)
        self.assertTrue(reasons, "the contract's own evidence must refuse this claim")
        self.assertTrue(any("languages.primary" in r for r in reasons))
        self.assertTrue(any("e2e.framework" in r for r in reasons))
        self.assertTrue(any("java" in r and "go" in r for r in reasons))
        self.assertTrue(any("cypress" in r and "playwright" in r for r in reasons))
        # And every refusal cites real evidence, not a bare assertion.
        self.assertIn("pom.xml", " ".join(reasons))
        self.assertIn("cypress.config.js", " ".join(reasons))


# ===========================================================================
# unverified -- AC-3's "a declared command that does not exist ... recorded
# as declared with its evidence, and flagged unverified"
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class UnverifiedCommands(unittest.TestCase):

    def test_a_repo_relative_command_path_that_does_not_exist_is_unverified(self):
        root, sha = _write_fixture({
            "pom.xml": ("<project><modelVersion>4.0.0</modelVersion>"
                        "<groupId>g</groupId><artifactId>a</artifactId>"
                        "<version>1</version></project>\n"),
            "src/main/java/App.java": "public class App {}\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with CONTRACT.open_tree(root, sha) as tree:
            # No mvnw tracked at all -- ./mvnw is a repo path that is not there.
            self.assertTrue(CONTRACT._compute_unverified("./mvnw test", tree))

    def test_a_tracked_wrapper_path_is_verified(self):
        root, sha = _write_fixture({
            "pom.xml": ("<project><modelVersion>4.0.0</modelVersion>"
                        "<groupId>g</groupId><artifactId>a</artifactId>"
                        "<version>1</version></project>\n"),
            "mvnw": "#!/bin/sh\necho mvnw\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with CONTRACT.open_tree(root, sha) as tree:
            self.assertFalse(CONTRACT._compute_unverified("./mvnw test", tree))

    def test_standard_toolchain_binaries_are_always_verified(self):
        root, sha = _write_fixture({"go.mod": "module x\n\ngo 1.21\n"})
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with CONTRACT.open_tree(root, sha) as tree:
            self.assertFalse(CONTRACT._compute_unverified("go test ./...", tree))
            self.assertFalse(CONTRACT._compute_unverified("mvn test", tree))

    def test_a_bare_binary_no_manifest_names_is_unverified(self):
        root, sha = _write_fixture({"go.mod": "module x\n\ngo 1.21\n", "main.go": "package main\n"})
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with CONTRACT.open_tree(root, sha) as tree:
            self.assertTrue(CONTRACT._compute_unverified("golangci-lint run ./...", tree))

    def test_a_bare_binary_named_in_a_manifest_is_verified(self):
        root, sha = _write_fixture({
            "go.mod": "module x\n\ngo 1.21\n// golangci-lint pinned via tooling\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with CONTRACT.open_tree(root, sha) as tree:
            self.assertFalse(CONTRACT._compute_unverified("golangci-lint run ./...", tree))

    def test_package_json_script_naming_a_missing_path_is_unverified_end_to_end(self):
        """The issue's own worked example, exercised through the real
        package.json -> build_contract path, not just the private
        _compute_unverified() helper called in isolation. The emitted
        `test.value.command` is the synthesized "npm run test" wrapper text
        -- what has to be judged is the SCRIPT BODY package.json actually
        declares underneath it.
        """
        root, sha = _write_fixture({
            "package.json": json.dumps({
                "name": "fixture-missing-script", "version": "1.0.0",
                "scripts": {"test": "./scripts/does-not-exist.sh"},
            }),
            "index.js": "module.exports = () => 1;\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        env = contract["test"]
        self.assertEqual(env["value"]["command"], "npm run test")
        self.assertEqual(env["candidates"][0]["source_kind"], "manifest_script")
        self.assertTrue(
            env["unverified"],
            "scripts.test names ./scripts/does-not-exist.sh, not tracked at this SHA")

    def test_package_json_script_naming_a_missing_binary_is_unverified_end_to_end(self):
        """Same failure state, the bare-binary variant the issue names
        verbatim: `"lint": "totally-absent-binary ."`. Guards against the
        narrower bug where a manifest-sourced command trivially "verifies"
        itself by reciting its own text back out of the very file it came
        from -- a real devDependency (eslint) stays correctly verified in
        the same fixture, proving the fix doesn't just always flag
        everything.
        """
        root, sha = _write_fixture({
            "package.json": json.dumps({
                "name": "fixture-missing-binary", "version": "1.0.0",
                "scripts": {
                    "lint": "totally-absent-binary .",
                    "format": "eslint --fix .",
                },
                "devDependencies": {"eslint": "^8.0.0"},
            }),
            "index.js": "module.exports = () => 1;\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        lint_env = contract["lint"]
        self.assertEqual(lint_env["value"]["command"], "npm run lint")
        self.assertTrue(
            lint_env["unverified"],
            "scripts.lint names a binary no tracked manifest declares")
        format_env = contract["format"]
        self.assertEqual(format_env["value"]["command"], "npm run format")
        self.assertFalse(
            format_env["unverified"],
            "scripts.format runs eslint, a real devDependency -- must stay verified")


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class PackageJsonCommandIsRealShellSyntax(unittest.TestCase):
    """A seat is told (bin/nightshift_seat_contract.py + the owning seat
    configs) to take a resolved `command` and run it VERBATIM -- so the
    string this module hands back must be directly executable shell text,
    never a human-readable annotation shaped like one. The prior wrapper,
    `f"npm run {script} (in {directory})"`, was not: the parenthesised
    aside is shell metacharacters, and `bash -c` on it fails with a syntax
    error before npm ever starts -- confirmed against a real, sealed,
    non-Nightshift testbed whose own e2e candidates took exactly this
    shape ('npm run cy:open (in itf)').
    """

    def test_root_package_json_command_has_no_parenthesised_annotation(self):
        root, sha = _write_fixture({
            "package.json": json.dumps({
                "name": "fixture-runnable-root", "version": "1.0.0",
                "scripts": {"test": "true"},
            }),
            "index.js": "module.exports = () => 1;\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        cmd = contract["test"]["value"]["command"]
        self.assertEqual(cmd, "npm run test")
        self.assertNotIn("(", cmd)
        self.assertNotIn(")", cmd)
        # Not just string shape -- an actual shell must accept it as ONE
        # valid, complete command (syntax-checked, `bash -n`, never
        # executed: this fixture carries no installed npm dependencies and
        # asserting real success would depend on npm/node being present on
        # whatever machine runs this suite, which nothing else here
        # requires).
        syntax = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_nested_package_json_command_uses_npm_prefix_not_a_broken_cd(self):
        """A package.json below the repo root (a JS package inside a
        polyglot or monorepo layout) must still resolve to ONE directly
        runnable command -- `npm --prefix <dir> run <script>` is npm's own
        documented way to do that without a `cd` the wrapper text would
        otherwise need to smuggle in unsafely.
        """
        root, sha = _write_fixture({
            "itf/package.json": json.dumps({
                "name": "fixture-runnable-nested", "version": "1.0.0",
                "scripts": {"test": "true"},
            }),
            "itf/index.js": "module.exports = () => 1;\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        cmd = contract["test"]["value"]["command"]
        self.assertEqual(cmd, "npm --prefix itf run test")
        self.assertNotIn("(", cmd)
        self.assertNotIn(")", cmd)
        syntax = subprocess.run(["bash", "-n", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)


# ===========================================================================
# A resolved package.json script must be able to run to completion --
# never a first-match pick of a watch-mode or interactive-GUI script when a
# real, terminating alternative sits in the very same file (round-2
# verifier finding: "npm run test:watch" / "cypress open" are valid shell
# syntax but never exit -- the same defect PackageJsonCommandIsRealShellSyntax
# fixed one layer up, one layer down).
# ===========================================================================

@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class PackageJsonNeverPicksAHangingScript(unittest.TestCase):

    def test_watch_variant_never_wins_over_the_plain_test_script(self):
        root, sha = _write_fixture({
            "package.json": json.dumps({
                "name": "fixture-jest-watch", "version": "1.0.0",
                "devDependencies": {"jest": "^29.0.0"},
                # "test:watch" is declared FIRST -- the exact JSON key
                # order the old `entries[0]` pick would have grabbed.
                "scripts": {"test:watch": "jest --watch", "test": "jest --ci"},
            }),
            "index.js": "module.exports = () => 1;\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)

        self.assertIn(contract["test"]["status"], ("observed", "declared"))
        self.assertEqual(contract["test"]["value"]["command"], "npm run test")
        self.assertNotIn("test:watch", contract["test"]["value"]["command"])

        # The excluded watch script must be traceable, not silently gone.
        rejected_scripts = " ".join(
            n for r in contract["test"]["rejected"] for n in r.get("notes", []))
        reasons = " ".join(r["reason"] for r in contract["test"]["rejected"])
        self.assertIn("test:watch", rejected_scripts + reasons)
        self.assertIn("non_terminating", reasons)

    def test_cypress_open_never_wins_over_cy_run_in_the_same_file(self):
        """The exact multi-script shape a round-2 verifier fixture proved
        broken: one package.json below the repo root declaring an
        interactive opener, an unrelated narrowly-scoped script, AND the
        real headless runner -- "cy:open" declared first in JSON key
        order must not be the pick.
        """
        root, sha = _write_fixture({
            "itf/package.json": json.dumps({
                "name": "itf",
                "scripts": {
                    "cy:open": "cypress open",
                    "cy:wallet": "cypress run --spec 'cypress/e2e/wallet/**'",
                    "cy:run": "cypress run --browser chrome",
                },
            }),
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)

        self.assertIn(contract["e2e"]["status"], ("observed", "declared"))
        cmd = contract["e2e"]["value"]["command"]
        self.assertEqual(cmd, "npm --prefix itf run cy:run")
        self.assertNotIn("cy:open", cmd)
        self.assertNotIn("cy:wallet", cmd)

        # "cy:open" is excluded FOR CAUSE (non-terminating); "cy:wallet"
        # merely loses the canonical-name tiebreak, so it is never even a
        # candidate worth flagging as rejected -- only "cy:open" should be.
        rejected_scripts = " ".join(
            n for r in contract["e2e"]["rejected"] for n in r.get("notes", []))
        self.assertIn("cy:open", rejected_scripts)
        self.assertNotIn("cy:wallet", rejected_scripts)

    def test_canonical_script_names_stay_in_sync_with_the_bucket_function(self):
        """`_CANONICAL_PKG_SCRIPT_KEYS` is a hand-lifted mirror of
        `_pkg_script_bucket`'s own exact-match branches -- guard against
        the two silently drifting apart: every key this map claims is
        canonical for a category must actually bucket into that same
        category through the real function.
        """
        for category, keys in CONTRACT._CANONICAL_PKG_SCRIPT_KEYS.items():
            for key in keys:
                self.assertEqual(
                    CONTRACT._pkg_script_bucket(key), category,
                    f"{key!r} claimed canonical for {category!r} but "
                    f"_pkg_script_bucket says {CONTRACT._pkg_script_bucket(key)!r}")

    def test_pkg_script_terminates_helper(self):
        t = CONTRACT._pkg_script_terminates
        self.assertFalse(t("test", "test:watch", "jest --watch"))
        self.assertFalse(t("test", "watch", "webpack --watch"))
        self.assertFalse(t("build", "dev", "nodemon server.js"))
        self.assertTrue(t("test", "test", "jest --ci"))
        self.assertFalse(t("e2e", "cy:open", "cypress open"))
        self.assertTrue(t("e2e", "cy:run", "cypress run"))
        # A GUI/watch marker outside its own category is not disqualifying
        # -- "open" only means something for e2e.
        self.assertTrue(t("build", "open", "webpack --mode production"))


@unittest.skipUnless(CONTRACT is not None, SKIP_REASON)
class DetectionScope(unittest.TestCase):
    """`_is_out_of_detection_scope` decides what counts as THIS project."""

    def test_fixture_and_vendor_directories_are_out_of_scope(self):
        for path in ("tests/fixtures/dogfood_b_java/pom.xml",
                     "testdata/sample/go.mod",
                     "src/__fixtures__/package.json",
                     "web/node_modules/left-pad/package.json",
                     "vendor/github.com/x/y.go",
                     "deep/nested/test_data/Cargo.toml"):
            with self.subTest(path=path):
                self.assertTrue(CONTRACT._is_out_of_detection_scope(path))

    def test_real_source_is_in_scope(self):
        for path in ("bin/nightshift_project_contract.py",
                     "tests/test_project_contract.py",
                     "pom.xml",
                     "src/main/java/App.java"):
            with self.subTest(path=path):
                self.assertFalse(CONTRACT._is_out_of_detection_scope(path))

    def test_only_directory_segments_are_matched_never_the_basename(self):
        """A file honestly named `fixtures.py` in real source still counts."""
        self.assertFalse(CONTRACT._is_out_of_detection_scope("app/fixtures.py"))
        self.assertFalse(CONTRACT._is_out_of_detection_scope("testdata.go"))

    def test_excluded_paths_are_filtered_out_but_not_hidden(self):
        """Detection ignores them; `build_excluded_dirs_field` still sees the
        full tree, so an excluded directory is reported, not silently dropped.
        """
        root, sha = _write_fixture({
            "main.py": "print(1)\n",
            "tests/fixtures/other/pom.xml": "<project/>\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        contract = detect(root, sha)
        self.assertEqual(contract["dependency_manifests"]["status"], "absent")
        langs = {e["language"] for e in contract["languages"]["value"]["all"]}
        self.assertEqual(langs, {"python"})


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
