#!/usr/bin/env python3
"""Issue #695 -- re-runnable mutation evidence for PG-3 and PG-4.

    python3 tests/mutation_evidence_pg3_pg4.py            # every case
    python3 tests/mutation_evidence_pg3_pg4.py PG3-E      # one case
    python3 tests/mutation_evidence_pg3_pg4.py --list

Each case names a defect, the exact production-source edit that injects
it, and the test that must go red because of it. The script applies the
edit, runs that test, and reports whether the expectation held.

It NEVER edits the checkout. `bin/` and `tests/` are copied to a fresh
temporary directory and the mutation is applied there -- byte-identical
source, so the evidence is the same, but a crashed or cancelled run
cannot leave a mutant in the tree, and concurrent work in the checkout
cannot contaminate the result. (During the original audit a sibling agent
was mutating `bin/nightshift_provenance.py` in the same checkout; the
first PG-3 sweep had to be discarded and re-run in isolation because of
it. Hence this default.)

`expect` is what the audit RECORDED, not a wish:
  "red"       the named test fails with the defect present. Mutation
              evidence in the strict sense.
  "green"     the named test PASSES with the defect present, and that is
              the recorded, explained finding -- see the case's `note`.
              Two cases are green on purpose: GIT_OPTIONAL_LOCKS=0 and
              --no-optional-locks are a redundant pair, so removing
              either alone genuinely does not change behaviour.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = "nightshift_publication.py"
PROV = "nightshift_provenance.py"

# --------------------------------------------------------------------------
# (id, file, anchor, replacement, [tests that must react], expect, note)
# --------------------------------------------------------------------------
CASES = [
    # ---------------- PG-3: the producer cannot approve its own work -------
    ("PG3-A", PUB,
     '''    if reviewer_canonical == canonicalize_identity(stage_row["producer_identity"]):
        _refuse("self_review", {"stage_id": stage_row["id"]})''',
     '''    if False:
        _refuse("self_review", {"stage_id": stage_row["id"]})''',
     ["test_publication_gateway.RefusalSelfReview."
      "test_producer_cannot_review_own_publication_and_writes_nothing"],
     "red", "guard 1 of 3: producer identity, at review()"),

    ("PG3-B", PUB,
     '''    if reviewer_run_id and reviewer_run_id == stage_row["producer_run_id"]:
        _refuse("self_review_same_run", {"stage_id": stage_row["id"]})''',
     '''    if False:
        _refuse("self_review_same_run", {"stage_id": stage_row["id"]})''',
     ["test_publication_gateway.RefusalSelfReview."
      "test_self_review_same_run_is_refused_and_writes_nothing"],
     "red", "guard 2 of 3: same producer_run_id, at review()"),

    ("PG3-C", PUB,
     '''    for ancestor_identity in _ancestry_producer_identities(cx, stage_row["id"]):
        if reviewer_canonical == canonicalize_identity(ancestor_identity):''',
     '''    for ancestor_identity in _ancestry_producer_identities(cx, stage_row["id"]):
        if False:''',
     ["test_publication_gateway.RefusalSelfReview."
      "test_self_review_via_lineage_is_refused_and_writes_nothing"],
     "red", "guard 3 of 3: laundering via derived_from_stage_id, at review()"),

    ("PG3-D", PUB,
     '''    if reviewer_canonical == canonicalize_identity(stage_row["producer_identity"]):''',
     '''    if reviewer_canonical == (stage_row["producer_identity"] or ""):''',
     ["test_publication_gateway.RefusalSelfReview."
      "test_producer_cannot_review_own_publication_and_writes_nothing"],
     "red", "canonicalization dropped -- 'Eng_Bob' vs 'eng_bob' would pass"),

    ("PG3-E", PUB,
     '''    # Re-assert self-review at approve (design: checked at review AND approve AND apply).
    _assert_not_self_review(cx, stage_row, canonical, reviewer_run_id)''',
     '''    # Re-assert self-review at approve (design: checked at review AND approve AND apply).''',
     ["test_pg3_selfreview_depth.SelfReviewIsReAssertedAtApprove"],
     "red", "GAP FOUND: nothing covered the approve()-time re-assertion "
            "(124 tests, OK). Closed by test_pg3_selfreview_depth."),

    ("PG3-F", PUB,
     '''    _assert_not_self_review(cx, presented_stage, auth["reviewer_identity"], None)''',
     '''    pass''',
     ["test_pg3_selfreview_depth.SelfReviewIsReAssertedAtApply"],
     "red", "GAP FOUND: nothing covered the apply()-time re-assertion "
            "(124 tests, OK). Closed by test_pg3_selfreview_depth."),

    ("PG3-G", PUB,
     '''        yield parent["producer_identity"]
        current = parent''',
     '''        yield parent["producer_identity"]
        current = None''',
     ["test_pg3_selfreview_depth.SelfReviewLineageWalkIsDeeperThanOneHop"],
     "red", "GAP FOUND: the ancestry walk was only proven one hop deep "
            "(124 tests, OK). Closed by test_pg3_selfreview_depth."),

    ("PG3-H", PUB,
     '''ANCESTRY_WALK_LIMIT = 64''',
     '''ANCESTRY_WALK_LIMIT = 4''',
     ["test_pg3_selfreview_depth.SelfReviewLineageWalkIsDeeperThanOneHop"],
     "red", "the walk bound itself. NOTE: the first version of the new test "
            "read the depth from NSP.ANCESTRY_WALK_LIMIT and therefore "
            "shrank WITH the defect -- a self-healing test, caught by this "
            "case and rewritten to use a literal."),

    # ---------------- PG-4: zero footprint ---------------------------------
    ("PG4-A", PROV,
     '''                if hash_files:
                    entry["sha256"] = _sha256_file(full)''',
     '''                if False:
                    entry["sha256"] = _sha256_file(full)''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4a_same_size_byte_change_inside_gitdir_is_a_violation"],
     "red", "GAP FOUND: the recursive .git CONTENT hash was never "
            "load-bearing (124 tests, OK) -- the existing 'changed' test "
            "appends bytes, so size alone caught it."),

    ("PG4-B", PROV,
     '''                    "size": st.st_size, "nlink": st.st_nlink,''',
     '''                    "size": st.st_size,''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4b_hardlinking_a_loose_object_is_a_violation_via_nlink"],
     "red", "GAP FOUND: st_nlink -- the module's own [VERIFIED] answer to "
            "E2, `git clone --bare` hardlinking customer loose objects -- "
            "was untested (124 tests, OK)."),

    ("PG4-C", PROV,
     '''            if e1 is None:
                violations.append({"manifest": name, "path": path, "kind": "added"})
                continue''',
     '''            if e1 is None:
                continue''',
     ["test_publication_footprint.CompareFootprintDetectsDirt"
      ".test_stray_file_planted_inside_gitdir_is_reported_as_a_violation"],
     "red", "an added file is a violation"),

    ("PG4-D", PROV,
     '''            if e2 is None:
                violations.append({"manifest": name, "path": path, "kind": "removed"})
                continue''',
     '''            if e2 is None:
                continue''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4d_a_file_removed_from_gitdir_is_a_violation"],
     "red", "GAP FOUND: deletion inside .git was unproven (124 tests, OK)."),

    ("PG4-E", PROV,
     '''            if e1c != e2c:
                violations.append({
                    "manifest": name, "path": path, "kind": "changed",''',
     '''            if False:
                violations.append({
                    "manifest": name, "path": path, "kind": "changed",''',
     ["test_publication_footprint.CompareFootprintDetectsDirt"
      ".test_byte_changed_inside_an_existing_gitdir_file_is_reported_as_a_violation"],
     "red", "a changed entry is a violation"),

    ("PG4-F", PROV,
     '''        if views_before.get(name) != views_after.get(name):
            violations.append({
                "manifest": "git_view", "path": name, "kind": "changed",''',
     '''        if False:
            violations.append({
                "manifest": "git_view", "path": name, "kind": "changed",''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4f_git_view_drift_alone_is_a_violation",
      "test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4f_every_corroborating_view_is_compared_not_just_one"],
     "red", "GAP FOUND: every existing call site fed compare_footprint a "
            "pristine pair, so the whole corroborating-views half of the "
            "oracle was untested (124 tests, OK)."),

    ("PG4-G", PROV,
     '''    _diff_manifest("gitdir", before.get("gitdir_manifest"), after.get("gitdir_manifest"))''',
     '''    pass''',
     ["test_publication_footprint.CompareFootprintDetectsDirt"],
     "red", "the gitdir manifest is compared"),

    ("PG4-H", PROV,
     '''    _diff_manifest("worktree", before.get("worktree_manifest"), after.get("worktree_manifest"))''',
     '''    pass''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4h_the_worktree_manifest_is_actually_compared"],
     "red", "GAP FOUND: the worktree half of compare_footprint had no test "
            "of its own (124 tests, OK) -- every existing worktree change "
            "also lands in .git/index via `git add`."),

    ("PG4-I", PROV,
     '''    if not self_proof_clean:
        raise ProvenanceError("footprint_capture_invalid", 1, {''',
     '''    if False:
        raise ProvenanceError("footprint_capture_invalid", 1, {''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4i_capture_refuses_its_own_evidence_if_its_own_step_perturbed_the_repo"],
     "red", "GAP FOUND: the self-proof was only ever read on a clean run, "
            "so the branch that refuses a poisoned capture was never "
            "taken (124 tests, OK)."),

    ("PG4-J", PROV,
     '''    if cmd not in _SIMPLE_ALLOWLIST:
        _refuse("customer_repo_write_refused", {''',
     '''    if False:
        _refuse("customer_repo_write_refused", {''',
     ["test_provenance.CustomerGitGuard.test_refuses_write_subcommands"],
     "red", "the read-only exec choke point refuses write verbs"),

    ("PG4-K", PROV,
     '''        if any(arg == p or arg.startswith(p) for p in _FORBIDDEN_ARG_PREFIXES):''',
     '''        if False:''',
     ["test_provenance.CustomerGitGuard.test_refuses_smuggled_config_and_dir_overrides"],
     "red", "-c / --git-dir / --work-tree / -C cannot be smuggled"),

    ("PG4-L", PROV,
     '''        if second not in _MULTI_WORD_ALLOWLIST[cmd]:''',
     '''        if False:''',
     ["test_provenance.CustomerGitGuard.test_refuses_multi_word_write_variants"],
     "red", "`worktree add` / `stash push` share argv[0] with a read verb"),

    ("PG4-M", PROV,
     '''    "status": ["status", "--porcelain=v1", "-z", "-uall", "--ignored=matching"],''',
     '''    "status": ["status", "--porcelain=v1", "-z", "-uall"],''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4m_the_footprint_status_view_reports_ignored_files"],
     "red", "GAP FOUND: the E6a/E6b tests exercise detect_dirty, which "
            "builds its own argv -- the footprint VIEW's argv was "
            "unproven (124 tests, OK), so it could go blind to an "
            "ignored `.omni/`."),

    ("PG4-N", PROV,
     '''    "GIT_OPTIONAL_LOCKS": "0",\n''', '''''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4no_the_guarded_status_does_not_rewrite_a_stale_index"],
     "green", "NOT A GAP: --no-optional-locks on argv still holds. "
              "Redundant pair, not dead code -- see PG4-NO."),

    ("PG4-O", PROV,
     '''    full = ["git", "--no-optional-locks", *args]''',
     '''    full = ["git", *args]''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4no_the_guarded_status_does_not_rewrite_a_stale_index"],
     "green", "NOT A GAP: GIT_OPTIONAL_LOCKS=0 in the env still holds. "
              "Redundant pair, not dead code -- see PG4-NO."),

    ("PG4-NO", PROV,
     '''    "GIT_OPTIONAL_LOCKS": "0",\n''', '''''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4no_the_guarded_status_does_not_rewrite_a_stale_index",
      "test_pg4_independent_footprint.DefaultModeWritesNothingIntoTheCustomerRepository"
      ".test_the_full_default_mode_cycle_leaves_the_customer_repo_byte_identical"],
     "red", "BOTH optional-lock controls removed at once (this case also "
            "strips --no-optional-locks from argv). This is what proves "
            "the pair is a real control rather than two no-ops."),

    ("PG4-P", PROV,
     '''            roots["worktree_root"], hash_files=(tier == "B"),''',
     '''            roots["worktree_root"], hash_files=False,''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4a_the_worktree_half_of_tier_b_also_hashes_content"],
     "red", "GAP FOUND: tier B's whole reason to exist -- the worktree "
            "CONTENT pass -- was unproven (124 tests, OK)."),

    ("PG4-Q", PROV,
     '''    common_after = (walk_manifest(roots["common_dir"], hash_files=True)
                     if roots["common_dir"] != roots["gitdir"] else None)''',
     '''    common_after = None''',
     ["test_pg4_independent_footprint.ProductionOracleGapsFoundByMutation"
      ".test_pg4q_the_common_dir_is_walked_when_it_differs_from_the_gitdir"],
     "red", "GAP FOUND: every fixture in the suite is an ordinary checkout "
            "where common_dir == gitdir, so the COMMON walk -- which on a "
            "linked worktree is where the object store lives -- was never "
            "exercised (124 tests, OK)."),
]

EXTRA_EDITS = {
    # PG4-NO removes the second half of the redundant pair as well.
    "PG4-NO": [(PROV, '''    full = ["git", "--no-optional-locks", *args]''',
                '''    full = ["git", *args]''')],
}


def build_sandbox():
    root = tempfile.mkdtemp(prefix="ns-mutation-evidence-")
    for sub in ("bin", "tests"):
        shutil.copytree(os.path.join(REPO, sub), os.path.join(root, sub),
                        ignore=shutil.ignore_patterns("__pycache__"))
    return root


def apply_edit(root, fname, old, new):
    path = os.path.join(root, "bin", fname)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    if src.count(old) != 1:
        raise SystemExit(
            f"anchor not unique in bin/{fname} ({src.count(old)} matches). The "
            f"production source moved; update this case rather than deleting it.")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src.replace(old, new, 1))


def run_case(case, verbose=False):
    case_id, fname, old, new, tests, expect, note = case
    root = build_sandbox()
    try:
        apply_edit(root, fname, old, new)
        for extra in EXTRA_EDITS.get(case_id, ()):
            apply_edit(root, *extra)
        started = time.time()
        proc = subprocess.run([sys.executable, "-m", "unittest", "-v"] + list(tests),
                              cwd=os.path.join(root, "tests"),
                              capture_output=True, text=True, timeout=1800)
        elapsed = time.time() - started
    finally:
        shutil.rmtree(root, ignore_errors=True)

    failing = [ln.strip() for ln in proc.stderr.splitlines()
               if ln.startswith(("FAIL: ", "ERROR: "))]
    got = "red" if failing else "green"
    ok = got == expect
    print(f"{'PASS' if ok else 'MISMATCH'}  {case_id:7s} "
          f"expect={expect:5s} got={got:5s}  ({elapsed:.1f}s)  bin/{fname}")
    print(f"          {note}")
    for name in tests:
        print(f"          test: {name}")
    for line in failing:
        print(f"          -> {line}")
    if verbose and not ok:
        print(proc.stderr)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", nargs="*", help="case ids (default: all)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.list:
        for c in CASES:
            print(f"{c[0]:7s} expect={c[5]:5s} bin/{c[1]}  {c[6]}")
        return 0

    wanted = [c for c in CASES if not args.cases or c[0] in args.cases]
    if not wanted:
        raise SystemExit(f"no such case: {args.cases}")
    results = [run_case(c, args.verbose) for c in wanted]
    print(f"\n{sum(results)}/{len(results)} cases behaved as recorded")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
