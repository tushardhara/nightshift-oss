"""Issue #400 -- the Publication Gateway (ADR-0006), PG-1/PG-2/PG-3/PG-5 slice.

`bin/nightshift_publication.py` is the ONLY component allowed to write to a
customer repository. This suite drives its Python API directly (not through
a subprocess CLI -- every entrypoint already takes an explicit `t`, exactly
the seam these tests need to move the clock without sleeping) against real
git fixtures: a real `git init --bare` destination repository built with
real plumbing subprocess calls (`hash-object`, `update-index`, `write-tree`,
`commit-tree`, `update-ref`), and a real SQLite `publication.db`. No mocks.

PG-2 is the module's own stated highest-risk line: `authorization
consumption is a single rowcount-gated UPDATE ... WHERE ... AND
consumed_at IS NULL AND revoked_at IS NULL` over the exact 7-tuple
(tenant_id, repository_id, content_digest, destination_path,
expected_base_sha, reviewer_identity, expiry). Six negative tests below
attack that CAS from six different angles -- reuse, a base that moved,
changed bytes, a changed path, expiry, and self-review -- and every one of
them asserts, via a git subprocess run directly against the bare
destination (never through the gateway), that HEAD and the commit count
did not move. A negative test that only checks "an exception was raised"
would still pass if the CAS's WHERE-clause predicate that makes THAT
specific case fail were deleted while some other predicate happened to
still throw first; checking the git-visible effect (or absence of one)
closes that gap -- the assertion is on the invariant PG-2 exists to
protect, not merely on the shape of the failure.

Companion is the happy path (stage -> review -> approve -> apply lands a
real commit, independently confirmed via `git log`/`git show` against the
bare repo) and PG-5's no_generated_file mode (approve refuses
`publication_disabled_no_generated_file` with zero authorization rows
created; `patch` still returns a correct unified diff in that same
strict-mode DB, and the schema's own CHECK constraint makes a strict-mode
policy structurally unable to name an egress target at all).

Every guard this suite exercises is named in the module's own docstrings
(`_assert_not_self_review`, `consume_publication_authorization`,
`_diagnose_authorization_failure`, the `no_generated_file` CHECK on
`publication_policy`) -- this file is written against that documented
contract, not against incidental implementation shape, so it keeps
detecting a removed guard even if the surrounding code is refactored.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import traceback
import sys
import tempfile
import unittest
from pathlib import Path
from publication_identities import producer_token  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
MODULE_PATH = BIN / "nightshift_publication.py"


_IMPORT_ERROR = None

def _load_publication_module():
    """Import bin/nightshift_publication.py by path -- same fail-soft
    convention as test_project_contract.py's `_load_contract_module`:
    returns None (never raises) whether the file is absent or fails to
    import, so the suite skips with a reason at run time instead of
    erroring at collection either way."""
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("nightshift_publication", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 -- "not ready yet" too
        global _IMPORT_ERROR
        _IMPORT_ERROR = traceback.format_exc()
        print(f"WARNING: {MODULE_PATH} failed to import: {exc}", file=sys.stderr)
        return None
    finally:
        sys.path.remove(str(BIN))
    return module


NSP = _load_publication_module()
# #694: nightshift_publication now IMPORTS nightshift_provenance and exposes
# it as `prov`. Reaching it through the module under test (rather than
# importing it a second time by path) is deliberate -- it asserts, by simply
# being how these fixtures get their snapshots, that the two modules really
# are one process talking to one validator.
PROV = getattr(NSP, "prov", None)
SKIP_REASON = (
    f"{MODULE_PATH} not importable -- issue #400 Publication Gateway "
    "(ADR-0006). This suite is written against the interface documented "
    "in that module's own docstrings.")

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               GIT_TERMINAL_PROMPT="0")

FIXTURE_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": "fixture-seed", "GIT_AUTHOR_EMAIL": "fixture-seed@test.invalid",
    "GIT_COMMITTER_NAME": "fixture-seed", "GIT_COMMITTER_EMAIL": "fixture-seed@test.invalid",
    "GIT_AUTHOR_DATE": "@1700000000 +0000", "GIT_COMMITTER_DATE": "@1700000000 +0000",
}

T0 = 2_000_000.0  # far from wall-clock epoch values, same rationale as ctlutil.VIRTUAL_T0
REF = "refs/heads/main"

# #400 item 10, structurally required by stage() itself since the PG-7 fix:
# every source_snapshot must carry these four identity fields. Fixed,
# well-formed placeholder values -- this file exercises PG-1/2/3/5, not the
# identity/closure semantics themselves (tests/test_provenance_lineage.py
# does that, against real git fixtures via nightshift_provenance.discover_
# closure and repository_fingerprint).
#
# Since #694 these are the fields the fixture writes into a REAL
# source_snapshot row in the sibling provenance.db; stage() then reads them
# back out of that row. They are no longer a dict handed to stage().
FIXTURE_SNAPSHOT_IDENTITY = {
    "repository_fingerprint": "sha256:" + "a" * 64,
    "object_format": "sha1",
    "commit_oid": "b" * 40,
    "tree_oid": "c" * 40,
}
FIXTURE_SNAPSHOT_ID = "snap-fixture"


# ---------------------------------------------------------------------------
# Real-git fixture helpers. Every one of these shells out to a real `git`
# binary against a real bare repository -- no in-process git simulation.
# ---------------------------------------------------------------------------

def _run_git(git_dir, args, *, input_bytes=None, env_extra=None, check=True):
    env = dict(GIT_ENV)
    env.update(env_extra or {})
    result = subprocess.run(["git", "--git-dir", git_dir] + list(args), input=input_bytes,
                             capture_output=True, env=env, timeout=30)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {args} failed (rc={result.returncode}): "
            f"{result.stderr.decode('utf-8', 'replace')}")
    return result


def _init_bare(base_dir):
    git_dir = os.path.join(base_dir, "dest.git")
    subprocess.run(["git", "init", "--quiet", "--bare", git_dir], check=True,
                    capture_output=True, env=GIT_ENV)
    return git_dir


def _seed_commit(git_dir, files, *, parent, index_path):
    """Build one commit via plumbing only (no working tree needed -- the
    destination is bare). `files`: {relative_path: bytes}. Returns the new
    commit sha. Mirrors nightshift_publication.py's own apply-time index
    trick exactly, so the fixture and the gateway agree on one mechanism."""
    if os.path.exists(index_path):
        os.remove(index_path)
    idx_env = {"GIT_INDEX_FILE": index_path}
    if parent:
        _run_git(git_dir, ["read-tree", parent], env_extra=idx_env)
    for path, content in files.items():
        blob = _run_git(git_dir, ["hash-object", "-w", "--stdin"],
                         input_bytes=content).stdout.strip().decode("ascii")
        _run_git(git_dir, ["update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
                  env_extra=idx_env)
    tree = _run_git(git_dir, ["write-tree"], env_extra=idx_env).stdout.strip().decode("ascii")
    commit_args = ["commit-tree", tree, "-m", "fixture seed"]
    if parent:
        commit_args += ["-p", parent]
    commit = _run_git(git_dir, commit_args, env_extra=FIXTURE_AUTHOR_ENV).stdout.strip().decode("ascii")
    os.remove(index_path)
    return commit


def _update_ref(git_dir, ref, new_sha, old_sha=None):
    args = ["update-ref", ref, new_sha]
    if old_sha is not None:
        args.append(old_sha)
    _run_git(git_dir, args)


def _ref_sha(git_dir, ref):
    result = _run_git(git_dir, ["rev-parse", "--verify", ref], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip().decode("ascii")


def _commit_count(git_dir, ref):
    result = _run_git(git_dir, ["rev-list", "--count", ref], check=False)
    if result.returncode != 0:
        return 0
    return int(result.stdout.strip())


def _blob_at(git_dir, commit, path):
    return _run_git(git_dir, ["show", f"{commit}:{path}"]).stdout


# ---------------------------------------------------------------------------
# A real `git` shim on PATH, for the L3 git-level CAS test only (see
# RefusalConcurrentGitLevelBaseMove below). Reconstructs the pattern
# tests/test_preflight.py already uses for a stubbed tool on an isolated
# PATH -- transparent `exec` passthrough for every invocation except one
# named subcommand, real subprocess calls throughout, nothing mocked.
# ---------------------------------------------------------------------------

_RACE_SHIM_BODY = """
set -u
is_commit_tree=""
if [ "${NIGHTSHIFT_TEST_INJECT_RACE:-}" = "1" ] && [ ! -e "${NIGHTSHIFT_TEST_RACE_MARKER:-/nonexistent}" ]; then
    for arg in "$@"; do
        if [ "$arg" = "commit-tree" ]; then
            is_commit_tree=1
        fi
    done
fi

if [ "$is_commit_tree" = "1" ]; then
    # Let the gateway's OWN commit-tree run for real first -- its result
    # is a genuine, unaffected commit object; the race is about which one
    # WINS the ref, not about corrupting the gateway's own computation.
    out="$("$REAL_GIT" "$@")"
    rc=$?
    if [ $rc -eq 0 ]; then
        : > "$NIGHTSHIFT_TEST_RACE_MARKER"
        # Land a second, independent commit on the SAME parent the
        # gateway observed at L1 -- a genuinely concurrent publisher,
        # using real plumbing directly against the destination, never
        # through the gateway -- inside the L1(observe)->L3(update-ref)
        # window this shim exists to open.
        idx="${NIGHTSHIFT_TEST_RACE_MARKER}.index"
        rival_blob=$(printf 'rival concurrent content\\n' | GIT_INDEX_FILE="$idx" "$REAL_GIT" --git-dir "$NIGHTSHIFT_TEST_RACE_GIT_DIR" hash-object -w --stdin)
        GIT_INDEX_FILE="$idx" "$REAL_GIT" --git-dir "$NIGHTSHIFT_TEST_RACE_GIT_DIR" read-tree "$NIGHTSHIFT_TEST_RACE_PARENT" >/dev/null
        GIT_INDEX_FILE="$idx" "$REAL_GIT" --git-dir "$NIGHTSHIFT_TEST_RACE_GIT_DIR" update-index --add --cacheinfo "100644,$rival_blob,rival.md" >/dev/null
        rival_tree=$(GIT_INDEX_FILE="$idx" "$REAL_GIT" --git-dir "$NIGHTSHIFT_TEST_RACE_GIT_DIR" write-tree)
        rival_commit=$(GIT_AUTHOR_NAME=rival-publisher GIT_AUTHOR_EMAIL=rival@test.invalid GIT_COMMITTER_NAME=rival-publisher GIT_COMMITTER_EMAIL=rival@test.invalid GIT_AUTHOR_DATE="@1700000500 +0000" GIT_COMMITTER_DATE="@1700000500 +0000" "$REAL_GIT" --git-dir "$NIGHTSHIFT_TEST_RACE_GIT_DIR" commit-tree "$rival_tree" -p "$NIGHTSHIFT_TEST_RACE_PARENT" -m "rival concurrent publish, landed inside the L1->L3 window")
        "$REAL_GIT" --git-dir "$NIGHTSHIFT_TEST_RACE_GIT_DIR" update-ref "$NIGHTSHIFT_TEST_RACE_REF" "$rival_commit" "$NIGHTSHIFT_TEST_RACE_PARENT" >/dev/null
        rm -f "$idx"
        printf '%s' "$rival_commit" > "${NIGHTSHIFT_TEST_RACE_MARKER}.rival_commit"
    fi
    printf '%s' "$out"
    exit "$rc"
fi

exec "$REAL_GIT" "$@"
"""


def _write_concurrent_git_race_shim(bin_dir):
    """Transparent for every invocation except `commit-tree`, which it
    intercepts exactly once (per NIGHTSHIFT_TEST_RACE_MARKER) to land a
    rival commit on the target ref before returning control to the
    gateway -- see _RACE_SHIM_BODY for the full mechanism."""
    real_git = shutil.which("git")
    assert real_git, "a real `git` must be on PATH to build the race shim"
    script = "#!/usr/bin/env bash\nREAL_GIT=" + shlex.quote(real_git) + "\n" + _RACE_SHIM_BODY
    shim_path = os.path.join(bin_dir, "git")
    with open(shim_path, "w") as fh:
        fh.write(script)
    os.chmod(shim_path, 0o755)
    return shim_path


# ---------------------------------------------------------------------------
# Base test case: one fresh state_root + one fresh bare destination repo per
# fixture, real SQLite, real git -- nothing shared across fixtures so a bug
# leaking state between tests cannot mask a missing guard.
# ---------------------------------------------------------------------------

@unittest.skipIf(NSP is None, SKIP_REASON)
class PublicationGatewayTestCase(unittest.TestCase):

    def _tmp(self, mode=0o700):
        d = tempfile.mkdtemp(prefix="nightshift-pub-test-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _new_fixture(self, *, mode="zero_nightshift_file", path_allowlist=("**",),
                      reviewer_identity="eng_bob", reviewer_token=b"tok-bob-secret",
                      seed_files=None):
        """One tenant/repository, one bare destination repo seeded with an
        initial commit on REF, one registered reviewer, policy set. Returns
        a dict of everything a test needs; every field is independently
        re-derivable via real git/sqlite so nothing here is a mock."""
        base_dir = self._tmp()
        state_root = os.path.join(base_dir, "state")
        os.makedirs(state_root, mode=0o700)
        repository_id = 1
        tenant_id = 1001

        git_dir = _init_bare(base_dir)
        index_path = os.path.join(base_dir, "seed-index")
        base_sha = _seed_commit(git_dir, seed_files or {"README.md": b"seed\n"},
                                 parent=None, index_path=index_path)
        _update_ref(git_dir, REF, base_sha)

        # #694: provenance.db comes first -- it is the database that
        # PRODUCES source_snapshot rows, and publication.db now sits beside
        # it in the same tenant-scoped directory and ATTACHes it read-only.
        prov_cx = PROV.connect(state_root, tenant_id, repository_id)
        self.addCleanup(prov_cx.close)
        PROV.record_source_snapshot(
            prov_cx, tenant_id, repository_id, FIXTURE_SNAPSHOT_ID,
            dict(FIXTURE_SNAPSHOT_IDENTITY, captured_at=T0))

        per_repo_root = NSP.resolve_publication_root(state_root, tenant_id, repository_id)
        cx = NSP.connect(per_repo_root)
        self.addCleanup(cx.close)

        target_git_dir = None if mode == "no_generated_file" else git_dir
        target_ref = None if mode == "no_generated_file" else REF
        NSP.policy_set(cx, tenant_id=tenant_id, repository_id=repository_id, mode=mode,
                        path_allowlist=list(path_allowlist), target_git_dir=target_git_dir,
                        target_ref=target_ref, key_version=None, t=T0)
        NSP.register_reviewer(cx, tenant_id=tenant_id, reviewer_identity=reviewer_identity,
                               token_bytes=reviewer_token, t=T0)

        return {
            "cx": cx, "state_root": per_repo_root, "git_dir": git_dir, "base_sha": base_sha,
            "tenant_id": tenant_id, "repository_id": repository_id,
            "reviewer_identity": reviewer_identity, "reviewer_token": reviewer_token,
            "seed_index_path": index_path,
            "prov_cx": prov_cx, "snapshot_id": FIXTURE_SNAPSHOT_ID,
            "unscoped_state_root": state_root,
        }

    def _record_snapshot(self, fx, snapshot_id, **overrides):
        """One more REAL provenance snapshot in the fixture's own tenant."""
        fields = dict(FIXTURE_SNAPSHOT_IDENTITY, captured_at=T0)
        fields.update(overrides)
        PROV.record_source_snapshot(
            fx["prov_cx"], fx["tenant_id"], fx["repository_id"], snapshot_id, fields)
        return snapshot_id

    def _stage_review_approve(self, fx, *, content=b"content A", path="docs/report.md",
                               producer="eng_alice", producer_run="run-1", ttl=3600,
                               t=T0, expected_base_sha=None, reviewer_run="rev-run-1"):
        cx = fx["cx"]
        st = NSP.stage(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=content, destination_path=path, classification="report",
                        producer_identity=producer,
                        producer_token_bytes=producer_token(NSP, cx, fx["tenant_id"], producer), producer_run_id=producer_run,
                        snapshot_id=fx["snapshot_id"], t=t)
        rv = NSP.review(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                         stage_id=st["stage_id"], reviewer_identity=fx["reviewer_identity"],
                         reviewer_run_id=reviewer_run, token_bytes=fx["reviewer_token"],
                         verdict="approve", t=t)
        ap = NSP.approve(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                          stage_id=st["stage_id"], review_id=rv["review_id"],
                          reviewer_identity=fx["reviewer_identity"], reviewer_run_id=reviewer_run,
                          token_bytes=fx["reviewer_token"],
                          expected_base_sha=expected_base_sha or fx["base_sha"], ttl=ttl, t=t)
        return st, rv, ap

    def _apply(self, fx, *, authorization_id, stage_id, idempotency_key, t):
        return NSP.apply_publication(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            authorization_id=authorization_id, stage_id=stage_id,
            idempotency_key=idempotency_key, state_root=fx["state_root"], t=t)

    def _assert_refusal(self, callable_, expected_reason_code):
        with self.assertRaises(NSP.PublicationError) as ctx:
            callable_()
        self.assertEqual(ctx.exception.reason_code, expected_reason_code)
        self.assertEqual(ctx.exception.exit_code, 3,
                          "a fail-closed refusal must be exit_code 3 (_refuse), never a bare raise")
        return ctx.exception


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class HappyPath(PublicationGatewayTestCase):

    def test_stage_review_approve_apply_lands_real_commit(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"content A",
                                                  path="docs/report.md")

        result = self._apply(fx, authorization_id=ap["authorization_id"],
                              stage_id=st["stage_id"], idempotency_key="idem-1", t=T0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "applied")

        # Independent confirmation, entirely outside the gateway.
        self.assertEqual(_ref_sha(fx["git_dir"], REF), result["commit_oid"])
        self.assertEqual(_commit_count(fx["git_dir"], REF), 2)  # seed + published
        self.assertEqual(_blob_at(fx["git_dir"], result["commit_oid"], "docs/report.md"),
                          b"content A")

    def test_idempotent_replay_returns_same_commit_without_a_second_write(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"content B", path="docs/b.md")
        first = self._apply(fx, authorization_id=ap["authorization_id"],
                             stage_id=st["stage_id"], idempotency_key="idem-same", t=T0)
        second = self._apply(fx, authorization_id=ap["authorization_id"],
                              stage_id=st["stage_id"], idempotency_key="idem-same", t=T0 + 1)
        self.assertEqual(first["commit_oid"], second["commit_oid"])
        self.assertEqual(_commit_count(fx["git_dir"], REF), 2, "replay must not add a commit")


# ---------------------------------------------------------------------------
# PG-7: derived_from lineage, wired to the real pipeline (not just the
# `publication_lineage` table shape -- a real chain produced by a real
# stage -> review -> approve -> apply run).
# ---------------------------------------------------------------------------

class PublicationLineage(PublicationGatewayTestCase):

    def _lineage_rows(self, fx):
        """Raw SQL against publication_lineage, independent of
        `publication_lineage_chain`'s own walk -- so this test cannot pass
        merely because the walker and the writer share the same bug."""
        return [dict(r) for r in fx["cx"].execute(
            "SELECT * FROM publication_lineage ORDER BY id ASC").fetchall()]

    def test_apply_publication_records_a_real_derived_from_chain(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"content C",
                                                  path="docs/c.md")
        result = self._apply(fx, authorization_id=ap["authorization_id"],
                              stage_id=st["stage_id"], idempotency_key="idem-lineage", t=T0)
        self.assertTrue(result["ok"])

        # Raw rows: exactly the three edges this run should have produced,
        # each written in the same transaction as the row it describes.
        rows = self._lineage_rows(fx)
        self.assertEqual(len(rows), 3, rows)
        by_child_kind = {r["child_kind"]: r for r in rows}
        self.assertEqual(by_child_kind["stage"]["child_id"], st["stage_id"])
        self.assertEqual(by_child_kind["stage"]["parent_kind"], "input_snapshot")
        self.assertEqual(by_child_kind["review"]["child_id"], rv["review_id"])
        self.assertEqual(by_child_kind["review"]["parent_kind"], "stage")
        self.assertEqual(by_child_kind["review"]["parent_ref"], str(st["stage_id"]))
        self.assertEqual(by_child_kind["attempt"]["child_id"], result["attempt_id"])
        self.assertEqual(by_child_kind["attempt"]["parent_kind"], "review")
        self.assertEqual(by_child_kind["attempt"]["parent_ref"], str(rv["review_id"]))
        for row in rows:
            self.assertEqual(row["tenant_id"], fx["tenant_id"])

        # The walker (what a real caller/inspector would use) agrees, and
        # closes all the way back to the input snapshot.
        chain = NSP.publication_lineage_chain(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            attempt_id=result["attempt_id"])
        self.assertTrue(chain["closed"], chain)
        self.assertEqual([link["kind"] for link in chain["chain"]],
                          ["attempt", "review", "stage", "input_snapshot"])
        self.assertEqual(chain["chain"][0]["id"], result["attempt_id"])
        self.assertEqual(chain["chain"][1]["id"], rv["review_id"])
        self.assertEqual(chain["chain"][2]["id"], st["stage_id"])

    def test_lineage_chain_reports_missing_link_when_never_applied(self):
        """A stage that only ever got reviewed (never approved/applied) has
        no attempt at all -- the walker must say so, not silently invent one
        or throw."""
        fx = self._new_fixture()
        cx = fx["cx"]
        st = NSP.stage(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"never applied", destination_path="docs/never.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, cx, fx["tenant_id"], "eng_alice"),
                        producer_run_id="run-x", snapshot_id=fx["snapshot_id"], t=T0)
        chain = NSP.publication_lineage_chain(
            cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"], attempt_id=999999)
        self.assertFalse(chain["closed"])
        self.assertEqual(chain["missing"], "attempt")
        self.assertEqual(chain["chain"], [])

    def test_source_not_retained_is_recorded_explicitly_on_the_input_snapshot_edge(self):
        """#694 strengthens this: the flag is no longer a `stage()` kwarg a
        caller could set to anything it liked. It is read off the provenance
        row, so the edge cannot claim the source bytes are retained when
        provenance has already expired them."""
        fx = self._new_fixture()
        cx = fx["cx"]
        expired_snapshot = self._record_snapshot(fx, "snap-expired")
        PROV.mark_source_not_retained(
            fx["prov_cx"], fx["tenant_id"], fx["repository_id"], expired_snapshot,
            "real_deletion", t=T0)
        expired = NSP.stage(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                             content_bytes=b"expired source", destination_path="docs/exp.md",
                             classification="report", producer_identity="eng_alice",
                             producer_token_bytes=producer_token(NSP, cx, fx["tenant_id"], "eng_alice"),
                             producer_run_id="run-exp", snapshot_id=expired_snapshot,
                             t=T0)
        retained = NSP.stage(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                              content_bytes=b"still retained", destination_path="docs/ret.md",
                              classification="report", producer_identity="eng_alice",
                              producer_token_bytes=producer_token(NSP, cx, fx["tenant_id"], "eng_alice"),
                              producer_run_id="run-ret", snapshot_id=fx["snapshot_id"],
                              t=T0)

        rows = {r["child_id"]: r for r in self._lineage_rows(fx) if r["child_kind"] == "stage"}
        self.assertEqual(rows[expired["stage_id"]]["source_not_retained"], 1)
        self.assertEqual(rows[retained["stage_id"]]["source_not_retained"], 0,
                          "sibling control: the flag is not a no-op default")


# ---------------------------------------------------------------------------
# PG-7 continued: #400 items 10-11, enforced structurally by stage() itself
# -- see nightshift_publication._validate_source_snapshot_identity /
# _require_source_snapshot_closure. Real submodule/LFS closure DISCOVERY
# against a real git fixture lives in tests/test_provenance_lineage.py
# (ClosureDiscoveryAndRequirement); this class proves the SAME
# identity/closure contract is actually REACHABLE from stage() -- the real
# stage -> review -> approve -> apply entrypoint every other test in this
# file drives -- not merely present in nightshift_provenance.py's own,
# separate module.
#
# CHANGED BY #694, deliberately and in one direction only -- EARLIER:
# stage() no longer accepts a `source_snapshot` dict the caller invented, so
# these cases are now driven by recording a REAL (deliberately defective)
# source_snapshot row in provenance.db and then trying to publish from it.
# Two of them consequently move layers, and both move from "publication
# refuses at stage time" to "provenance refuses at write time", which is
# strictly earlier and strictly harder to route around:
#   * object_format: provenance's CHECK(object_format IN ('sha1','sha256'))
#     means a snapshot with a bad format cannot be RECORDED at all, so it
#     can never reach stage().
#   * a snapshot with NO identity at all: same reason -- object_format is
#     the one identity field the schema will not let be empty. The other
#     three still can be (NOT NULL does not stop ''), so the
#     `source_snapshot_incomplete` refusal below fires against a genuinely
#     provenance-produced row.
# ---------------------------------------------------------------------------

class SourceSnapshotIdentityAndClosure(PublicationGatewayTestCase):

    def test_stage_refuses_when_identity_fields_are_missing(self):
        fx = self._new_fixture()
        empty_identity = self._record_snapshot(
            fx, "snap-no-identity",
            repository_fingerprint="", commit_oid="", tree_oid="")
        exc = self._assert_refusal(
            lambda: NSP.stage(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                content_bytes=b"content", destination_path="docs/a.md",
                classification="report", producer_identity="eng_alice",
                producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                producer_run_id="run-1", snapshot_id=empty_identity, t=T0),
            "source_snapshot_incomplete")
        self.assertEqual(
            sorted(exc.details["missing"]),
            ["commit_oid", "repository_fingerprint", "tree_oid"])
        self.assertEqual(
            fx["cx"].execute("SELECT COUNT(*) AS n FROM publication_stage").fetchone()["n"], 0,
            "a refused stage must write no row at all")
        # The fourth identity field is not untested, it is enforced one
        # layer earlier: provenance will not record the row at all.
        with self.assertRaises(PROV.ProvenanceError) as ctx:
            self._record_snapshot(fx, "snap-no-format", object_format="")
        self.assertEqual(ctx.exception.reason_code, "source_snapshot_invalid")

    def test_stage_refuses_a_partially_populated_identity(self):
        """One missing field is still a refusal -- not merely 'all or
        nothing' by coincidence of an empty dict."""
        fx = self._new_fixture()
        partial = self._record_snapshot(fx, "snap-partial", tree_oid="")
        exc = self._assert_refusal(
            lambda: NSP.stage(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                content_bytes=b"content", destination_path="docs/a.md",
                classification="report", producer_identity="eng_alice",
                producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                producer_run_id="run-1", snapshot_id=partial, t=T0),
            "source_snapshot_incomplete")
        self.assertEqual(exc.details["missing"], ["tree_oid"])

    def test_stage_refuses_an_invalid_object_format(self):
        """Post-#694 this is unreachable from stage(), because provenance
        refuses to record the snapshot in the first place -- so the test
        asserts BOTH halves: the write is refused, and the id that was
        therefore never created cannot be published from."""
        fx = self._new_fixture()
        with self.assertRaises(PROV.ProvenanceError) as ctx:
            self._record_snapshot(fx, "snap-bad-format", object_format="sha3")
        self.assertEqual(ctx.exception.reason_code, "source_snapshot_invalid")
        self._assert_refusal(
            lambda: NSP.stage(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                content_bytes=b"content", destination_path="docs/a.md",
                classification="report", producer_identity="eng_alice",
                producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                producer_run_id="run-1", snapshot_id="snap-bad-format", t=T0),
            "source_snapshot_not_found")

    def test_stage_records_the_identity_fields_verbatim_on_a_real_publication(self):
        """The structural guarantee end to end: a real stage() call's
        denormalized snapshot identity, read back off the row a real caller
        would inspect, actually carries the identity fields PROVENANCE
        recorded -- not merely that stage() happened to accept the call.
        The copy is what keeps the stage row self-describing after
        `mark_source_not_retained` expires the snapshot (ADR-0079)."""
        fx = self._new_fixture()
        st = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"content", destination_path="docs/a.md",
            classification="report", producer_identity="eng_alice",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
            producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        row = NSP.get_stage(fx["cx"], st["stage_id"])
        self.assertEqual(row["source_snapshot_id"], fx["snapshot_id"])
        recorded = {
            "repository_fingerprint": row["repository_fingerprint"],
            "object_format": row["snapshot_object_format"],
            "commit_oid": row["snapshot_commit_oid"],
            "tree_oid": row["snapshot_tree_oid"],
        }
        for key, value in FIXTURE_SNAPSHOT_IDENTITY.items():
            self.assertEqual(recorded[key], value)

    def test_stage_refuses_closure_incomplete_when_a_required_path_is_missing(self):
        """#694: the requirement is no longer a caller's declaration. The
        snapshot was RECORDED with a frozen closure manifest digest covering
        two entries; provenance.db holds only one of them, so publication
        refuses -- and there is no dict a caller could pass to say otherwise."""
        fx = self._new_fixture()
        manifest = [
            {"kind": "submodule", "path": "vendor/sub", "oid": "d" * 40},
            {"kind": "lfs", "path": "assets/large.bin", "oid": "e" * 40},
        ]
        snapshot = self._record_snapshot(
            fx, "snap-closure-partial",
            closure_manifest_digest=PROV.closure_manifest_digest(manifest))
        PROV.record_closure_entry(
            fx["prov_cx"], fx["tenant_id"], fx["repository_id"], snapshot,
            "submodule", "vendor/sub", "d" * 40, t=T0)
        exc = self._assert_refusal(
            lambda: NSP.stage(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                content_bytes=b"content", destination_path="docs/a.md",
                classification="report", producer_identity="eng_alice",
                producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                producer_run_id="run-1", snapshot_id=snapshot, t=T0),
            "closure_incomplete")
        self.assertEqual(exc.details["covered_paths"], ["vendor/sub"])
        self.assertNotIn("assets/large.bin", exc.details["covered_paths"])
        self.assertNotEqual(exc.details["recorded_manifest_digest"],
                             exc.details["observed_manifest_digest"])
        self.assertEqual(
            fx["cx"].execute("SELECT COUNT(*) AS n FROM publication_stage").fetchone()["n"], 0,
            "a refused stage must write no row at all")

    def test_stage_succeeds_when_the_declared_closure_is_fully_covered(self):
        fx = self._new_fixture()
        manifest = [
            {"kind": "submodule", "path": "vendor/sub", "oid": "d" * 40},
            {"kind": "lfs", "path": "assets/large.bin", "oid": "e" * 40},
        ]
        snapshot = self._record_snapshot(
            fx, "snap-closure-complete",
            closure_manifest_digest=PROV.closure_manifest_digest(manifest))
        for entry in manifest:
            PROV.record_closure_entry(
                fx["prov_cx"], fx["tenant_id"], fx["repository_id"], snapshot,
                entry["kind"], entry["path"], entry["oid"], t=T0)
        st = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"content", destination_path="docs/a.md",
            classification="report", producer_identity="eng_alice",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
            producer_run_id="run-1", snapshot_id=snapshot, t=T0)
        self.assertEqual(st["status"], "staged")

    def test_stage_with_no_declared_required_paths_is_unaffected(self):
        """The common case -- no submodule/LFS content in scope for this
        publication -- is not forced to declare an empty closure at all."""
        fx = self._new_fixture()
        st = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"content", destination_path="docs/a.md",
            classification="report", producer_identity="eng_alice",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
            producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        self.assertEqual(st["status"], "staged")


# ---------------------------------------------------------------------------
# PG-5: no_generated_file (strict) mode
# ---------------------------------------------------------------------------

class NoGeneratedFileMode(PublicationGatewayTestCase):

    def test_approve_refuses_and_patch_still_returns_a_diff(self):
        fx = self._new_fixture(mode="no_generated_file")
        cx = fx["cx"]
        st = NSP.stage(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"strict mode content\n", destination_path="docs/s.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, cx, fx["tenant_id"], "eng_alice"),
                        producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)
        rv = NSP.review(cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                         stage_id=st["stage_id"], reviewer_identity=fx["reviewer_identity"],
                         reviewer_run_id="rev-run-1", token_bytes=fx["reviewer_token"],
                         verdict="approve", t=T0)

        self._assert_refusal(
            lambda: NSP.approve(
                cx, tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], review_id=rv["review_id"],
                reviewer_identity=fx["reviewer_identity"], reviewer_run_id="rev-run-1",
                token_bytes=fx["reviewer_token"], expected_base_sha=fx["base_sha"],
                ttl=3600, t=T0),
            "publication_disabled_no_generated_file")

        # No authorization row was created -- the refusal happened before
        # any row was written, not merely "the caller got an error back".
        count = cx.execute("SELECT COUNT(*) FROM publication_authorizations").fetchone()[0]
        self.assertEqual(count, 0)

        patch = NSP.patch_stage(cx, stage_id=st["stage_id"], tenant_id=fx["tenant_id"],
                                 repository_id=fx["repository_id"])
        self.assertTrue(patch["ok"])
        self.assertIn("+strict mode content", patch["patch"])

    def test_policy_cannot_name_an_egress_target_in_strict_mode(self):
        """Schema-level backstop for PG-5: even a direct SQL INSERT that
        tries to smuggle a target_git_dir into a no_generated_file policy
        row is refused by SQLite's own CHECK constraint, not by
        application code that a caller could route around."""
        fx = self._new_fixture(mode="no_generated_file")
        cx = fx["cx"]
        with self.assertRaises(Exception) as ctx:
            cx.execute(
                "UPDATE publication_policy SET target_git_dir=?, target_ref=? WHERE id=1",
                (fx["git_dir"], REF))
        self.assertIn("CHECK", str(ctx.exception).upper())


# ---------------------------------------------------------------------------
# PG-2 / PG-3: the six required refusals. Each is its own fixture so no
# state leaks between them. Each asserts (a) the exact reason_code, and
# (b) via a git subprocess run directly against the bare destination --
# never through the gateway -- that HEAD/the commit count did not move.
# ---------------------------------------------------------------------------

class RefusalReuseOfConsumedAuthorization(PublicationGatewayTestCase):

    def test_second_apply_with_same_authorization_is_refused_and_writes_nothing(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"content A",
                                                  path="docs/report.md")
        first = self._apply(fx, authorization_id=ap["authorization_id"],
                             stage_id=st["stage_id"], idempotency_key="idem-1", t=T0)
        self.assertEqual(_ref_sha(fx["git_dir"], REF), first["commit_oid"])

        # A real `apply` always moves the target ref forward, so on its own
        # a second attempt would ALSO be refused by the expected_base_sha
        # check (base_moved) -- that would mask whether the one-use
        # (consumed_at/revoked_at) predicate is doing anything at all.
        # Isolate it: an operator force-resets the ref back to the exact
        # SHA the authorization was issued against (e.g. reverting the
        # publish), via plumbing directly against the destination -- never
        # through the gateway. Now expected_base_sha, content_digest and
        # destination_path all match again; only the one-use guard stands
        # between a replay and a second write.
        _update_ref(fx["git_dir"], REF, fx["base_sha"], old_sha=first["commit_oid"])
        head_before_replay = _ref_sha(fx["git_dir"], REF)
        self.assertEqual(head_before_replay, fx["base_sha"])
        count_before_replay = _commit_count(fx["git_dir"], REF)

        # Same authorization_id, a DIFFERENT idempotency_key -- this is a
        # genuine reuse attempt, not the idempotent-replay path (which is
        # exercised separately in HappyPath and must stay a success).
        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                 stage_id=st["stage_id"], idempotency_key="idem-2", t=T0 + 1),
            "authorization_already_consumed")

        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before_replay,
                          "HEAD must not move on a reuse attempt")
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before_replay,
                          "reuse must not add a commit")


class RefusalStaleBase(PublicationGatewayTestCase):

    def test_base_moved_between_approval_and_apply_is_refused_and_writes_nothing(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"content A",
                                                  path="docs/report.md")

        # Someone else publishes directly to the destination in between --
        # the ref moves out from under the already-issued authorization.
        index_path = os.path.join(os.path.dirname(fx["seed_index_path"]), "drift-index")
        moved_sha = _seed_commit(fx["git_dir"], {"other.md": b"unrelated change\n"},
                                  parent=fx["base_sha"], index_path=index_path)
        _update_ref(fx["git_dir"], REF, moved_sha, old_sha=fx["base_sha"])
        head_before_apply = _ref_sha(fx["git_dir"], REF)
        self.assertEqual(head_before_apply, moved_sha)
        count_before_apply = _commit_count(fx["git_dir"], REF)

        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                 stage_id=st["stage_id"], idempotency_key="idem-1", t=T0 + 1),
            "base_moved")

        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before_apply,
                          "HEAD must not move (or move again) on a stale-base apply attempt")
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before_apply)


class RefusalConcurrentGitLevelBaseMove(PublicationGatewayTestCase):
    """The L3 git-level CAS -- the old-value argument on the gateway's own
    `update-ref` call, distinct from (and the ONLY thing that can catch a
    race in) the window `RefusalStaleBase` above cannot reach: that test's
    "someone else publishes" happens entirely BEFORE `apply_publication`
    is ever called, so it is caught by the SQLite CAS's `observed_base_sha`
    predicate at L1/T1. A genuine second Nightshift process racing this
    one shares no SQLite lock (different state_root) -- the module's own
    comment at the update-ref call site names this exact scenario -- so
    the ONLY thing that can catch a rival commit landing strictly BETWEEN
    this process's L1 `_rev_parse_commit` observe and its own final
    `update-ref` write is git's own compare-and-swap, via the old-value
    argument on that call. This test opens exactly that window with a
    real `git` shim on PATH (see `_write_concurrent_git_race_shim`) that
    lands a real, independent commit at the gateway's own `commit-tree`
    call -- one step before its `update-ref` -- so the SQLite CAS has
    already succeeded (observed_base_sha matched at T1) by the time the
    rival lands; only the L3 argument stands between the rival commit and
    silent data loss.
    """

    def test_rival_commit_landed_inside_the_window_wins_and_gateway_refuses_cleanly(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"gateway's own content",
                                                  path="docs/report.md")
        count_before = _commit_count(fx["git_dir"], REF)
        self.assertEqual(_ref_sha(fx["git_dir"], REF), fx["base_sha"])

        bin_dir = self._tmp()
        _write_concurrent_git_race_shim(bin_dir)
        marker = os.path.join(self._tmp(), "race-marker")

        original_path = os.environ.get("PATH", "")
        original_env = {k: os.environ.get(k) for k in (
            "NIGHTSHIFT_TEST_INJECT_RACE", "NIGHTSHIFT_TEST_RACE_GIT_DIR",
            "NIGHTSHIFT_TEST_RACE_REF", "NIGHTSHIFT_TEST_RACE_PARENT",
            "NIGHTSHIFT_TEST_RACE_MARKER")}
        os.environ["PATH"] = bin_dir + os.pathsep + original_path
        os.environ["NIGHTSHIFT_TEST_INJECT_RACE"] = "1"
        os.environ["NIGHTSHIFT_TEST_RACE_GIT_DIR"] = fx["git_dir"]
        os.environ["NIGHTSHIFT_TEST_RACE_REF"] = REF
        os.environ["NIGHTSHIFT_TEST_RACE_PARENT"] = fx["base_sha"]
        os.environ["NIGHTSHIFT_TEST_RACE_MARKER"] = marker
        try:
            self._assert_refusal(
                lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                     stage_id=st["stage_id"], idempotency_key="idem-race", t=T0 + 1),
                "base_moved_during_apply")
        finally:
            os.environ["PATH"] = original_path
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertTrue(os.path.exists(marker), "the shim must actually have fired the race")
        with open(marker + ".rival_commit") as fh:
            rival_commit = fh.read().strip()
        self.assertTrue(rival_commit)

        # The rival's commit -- landed by a process holding no SQLite lock
        # this gateway shares -- must be exactly what survives as HEAD:
        # neither reverted nor silently fast-forwarded past.
        self.assertEqual(_ref_sha(fx["git_dir"], REF), rival_commit,
                          "the concurrently-landed commit must survive untouched")
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before + 1,
                          "exactly the rival's one commit, and nothing from the gateway, applied")
        self.assertEqual(_blob_at(fx["git_dir"], rival_commit, "rival.md"),
                          b"rival concurrent content\n")
        # The gateway's own write must never have landed anywhere reachable.
        leaked = _run_git(fx["git_dir"], ["cat-file", "-e", f"{rival_commit}:docs/report.md"],
                           check=False)
        self.assertNotEqual(leaked.returncode, 0,
                             "the gateway's own content must not be reachable from HEAD")

        # The attempt row itself must record the real terminal state --
        # 'conflict', not 'failed' -- and the DB-visible reason code.
        attempt_row = fx["cx"].execute(
            "SELECT state, reason_code FROM publication_attempts WHERE stage_id=? "
            "ORDER BY id DESC LIMIT 1", (st["stage_id"],)).fetchone()
        self.assertEqual(attempt_row["state"], "conflict")
        self.assertEqual(attempt_row["reason_code"], "base_moved_during_apply")


class RefusalChangedContentByte(PublicationGatewayTestCase):

    def test_apply_against_a_different_staged_byte_is_refused_and_writes_nothing(self):
        fx = self._new_fixture()
        st_a, rv_a, ap_a = self._stage_review_approve(fx, content=b"content X",
                                                        path="docs/x.md")
        head_before = _ref_sha(fx["git_dir"], REF)
        count_before = _commit_count(fx["git_dir"], REF)

        # Re-staged at the SAME path with a changed byte -- a new stage row
        # (stage rows are immutable), never reviewed or approved on its own.
        st_b = NSP.stage(fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                          content_bytes=b"content X -- TAMPERED", destination_path="docs/x.md",
                          classification="report", producer_identity="eng_alice",
                          producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                          producer_run_id="run-2", snapshot_id=fx["snapshot_id"], t=T0)
        self.assertNotEqual(st_b["content_digest"], st_a["content_digest"])

        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap_a["authorization_id"],
                                 stage_id=st_b["stage_id"], idempotency_key="idem-1", t=T0 + 1),
            "content_digest_mismatch")

        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before)


class RefusalChangedDestinationPath(PublicationGatewayTestCase):

    def test_apply_against_a_different_staged_path_is_refused_and_writes_nothing(self):
        fx = self._new_fixture()
        st_a, rv_a, ap_a = self._stage_review_approve(fx, content=b"identical bytes",
                                                        path="docs/y.md")
        head_before = _ref_sha(fx["git_dir"], REF)
        count_before = _commit_count(fx["git_dir"], REF)

        # Identical bytes (same content_digest), a DIFFERENT destination
        # path -- isolates the path check from the digest check.
        st_c = NSP.stage(fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                          content_bytes=b"identical bytes", destination_path="docs/y-alt.md",
                          classification="report", producer_identity="eng_alice",
                          producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                          producer_run_id="run-3", snapshot_id=fx["snapshot_id"], t=T0)
        self.assertEqual(st_c["content_digest"], st_a["content_digest"])

        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap_a["authorization_id"],
                                 stage_id=st_c["stage_id"], idempotency_key="idem-1", t=T0 + 1),
            "destination_path_mismatch")

        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before)


class RefusalExpiredAuthorization(PublicationGatewayTestCase):

    def test_apply_after_ttl_elapsed_is_refused_and_writes_nothing(self):
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"content A",
                                                  path="docs/report.md", ttl=5, t=T0)
        head_before = _ref_sha(fx["git_dir"], REF)
        count_before = _commit_count(fx["git_dir"], REF)
        self.assertEqual(ap["expires_at"], T0 + 5)

        # No sleeping -- the clock is an explicit argument. t = T0 + 10 is
        # well past expires_at = T0 + 5.
        self._assert_refusal(
            lambda: self._apply(fx, authorization_id=ap["authorization_id"],
                                 stage_id=st["stage_id"], idempotency_key="idem-1", t=T0 + 10),
            "authorization_expired")

        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before)

    def test_apply_one_tick_before_expiry_still_succeeds(self):
        """Sibling control: proves the expired-test above is actually
        exercising the TTL boundary and not some unrelated failure -- if
        the expiry guard were miscoded to always refuse, this would catch
        it just as the other test catches the guard being deleted."""
        fx = self._new_fixture()
        st, rv, ap = self._stage_review_approve(fx, content=b"content A",
                                                  path="docs/report.md", ttl=5, t=T0)
        result = self._apply(fx, authorization_id=ap["authorization_id"],
                              stage_id=st["stage_id"], idempotency_key="idem-1", t=T0 + 4.9)
        self.assertTrue(result["ok"])


class RefusalSelfReview(PublicationGatewayTestCase):

    def test_producer_cannot_review_own_publication_and_writes_nothing(self):
        fx = self._new_fixture(reviewer_identity="eng_bob")
        head_before = _ref_sha(fx["git_dir"], REF)
        count_before = _commit_count(fx["git_dir"], REF)

        # Case-folded identity collision, exactly the shape #400 item 14
        # calls out: the producer is "Eng_Bob", the reviewer is "eng_bob" --
        # a raw string compare would miss this; canonicalize_identity must
        # not.
        st = NSP.stage(fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"content A", destination_path="docs/report.md",
                        classification="report", producer_identity="Eng_Bob", producer_token_bytes=fx["reviewer_token"],
                        producer_run_id="run-1", snapshot_id=fx["snapshot_id"], t=T0)

        self._assert_refusal(
            lambda: NSP.review(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], reviewer_identity="eng_bob", reviewer_run_id="rev-run-1",
                token_bytes=fx["reviewer_token"], verdict="approve", t=T0),
            "self_review")

        review_count = fx["cx"].execute(
            "SELECT COUNT(*) FROM publication_review WHERE stage_id=?",
            (st["stage_id"],)).fetchone()[0]
        self.assertEqual(review_count, 0, "no review row may be created on a self-review attempt")

        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before)

    def test_a_different_reviewer_is_unaffected(self):
        """Sibling control for the self-review test: a genuinely different
        reviewer identity must NOT be caught by the guard, or this suite
        would be unable to distinguish 'self-review correctly refused'
        from 'the gateway refuses everyone'."""
        fx = self._new_fixture(reviewer_identity="eng_carol", reviewer_token=b"tok-carol")
        st, rv, ap = self._stage_review_approve(
            fx, content=b"content A", path="docs/report.md", producer="eng_alice")
        self.assertEqual(rv["verdict"], "approve")
        result = self._apply(fx, authorization_id=ap["authorization_id"],
                              stage_id=st["stage_id"], idempotency_key="idem-1", t=T0)
        self.assertTrue(result["ok"])

    def test_self_review_same_run_is_refused_and_writes_nothing(self):
        """PG-3, guard 2 of 3 (`self_review_same_run`): the producer and
        reviewer identities are genuinely DIFFERENT strings -- so the
        primary identity check (exercised above) cannot be what catches
        this -- and there is no derived_from_stage_id chain for the
        ancestry check to walk either. Only a shared reviewer_run_id /
        producer_run_id (the same CI job re-entering under a different
        login) closes this one; isolating it this way means the guard at
        review()'s line ~995-996 specifically is what this test pins."""
        fx = self._new_fixture(reviewer_identity="eng_bob")
        head_before = _ref_sha(fx["git_dir"], REF)
        count_before = _commit_count(fx["git_dir"], REF)

        st = NSP.stage(fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                        content_bytes=b"content A", destination_path="docs/report.md",
                        classification="report", producer_identity="eng_alice",
                        producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"),
                        producer_run_id="ci-run-77",
                        snapshot_id=fx["snapshot_id"], t=T0)

        self._assert_refusal(
            lambda: NSP.review(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st["stage_id"], reviewer_identity="eng_bob",
                reviewer_run_id="ci-run-77",  # same run_id as the producer -- different name
                token_bytes=fx["reviewer_token"], verdict="approve", t=T0),
            "self_review_same_run")

        review_count = fx["cx"].execute(
            "SELECT COUNT(*) FROM publication_review WHERE stage_id=?",
            (st["stage_id"],)).fetchone()[0]
        self.assertEqual(review_count, 0,
                          "no review row may be created on a same-run self-review attempt")
        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before)

    def test_self_review_via_lineage_is_refused_and_writes_nothing(self):
        """PG-3, guard 3 of 3 (`self_review_via_lineage`), #400 item 14's
        laundering shape: eng_alice stages content; eng_bob re-stages a
        derived version whose `derived_from_stage_id` points at alice's
        stage (`stage()`'s own public, documented parameter -- a
        legitimate shape on its own, e.g. a second worker incorporating
        alice's draft). Alice then reviews BOB's stage. Neither of the
        other two guards can catch this: her identity does not match the
        stage's OWN producer_identity (bob's), and the two stages carry
        different run_ids -- only the ancestry walk over
        derived_from_stage_id closes it."""
        fx = self._new_fixture(reviewer_identity="eng_bob")
        head_before = _ref_sha(fx["git_dir"], REF)
        count_before = _commit_count(fx["git_dir"], REF)
        alice_token = producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice", t=T0)

        st_alice = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"alice's draft", destination_path="docs/report.md",
            classification="report", producer_identity="eng_alice",
            producer_token_bytes=producer_token(NSP, fx["cx"], fx["tenant_id"], "eng_alice"), producer_run_id="run-alice",
            snapshot_id=fx["snapshot_id"], t=T0)
        st_bob = NSP.stage(
            fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
            content_bytes=b"bob's laundered version", destination_path="docs/report.md",
            classification="report", producer_identity="eng_bob", producer_token_bytes=fx["reviewer_token"], producer_run_id="run-bob",
            derived_from_stage_id=st_alice["stage_id"],
            snapshot_id=fx["snapshot_id"], t=T0)

        self._assert_refusal(
            lambda: NSP.review(
                fx["cx"], tenant_id=fx["tenant_id"], repository_id=fx["repository_id"],
                stage_id=st_bob["stage_id"], reviewer_identity="eng_alice",
                reviewer_run_id="rev-run-alice", token_bytes=alice_token, verdict="approve", t=T0),
            "self_review_via_lineage")

        review_count = fx["cx"].execute(
            "SELECT COUNT(*) FROM publication_review WHERE stage_id=?",
            (st_bob["stage_id"],)).fetchone()[0]
        self.assertEqual(review_count, 0,
                          "no review row may be created on a laundered self-review attempt")
        self.assertEqual(_ref_sha(fx["git_dir"], REF), head_before)
        self.assertEqual(_commit_count(fx["git_dir"], REF), count_before)



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
