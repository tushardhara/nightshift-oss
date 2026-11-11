#!/usr/bin/env python3
"""The DoD close-out gate.

Reads no configuration but `docs/prd/DOD-MANIFEST.json`; verifies every one of
the 24 conditions in `docs/prd/DEFINITION-OF-DONE.md` (v2) and prints exactly
one line per condition, `D01`..`D24`, each `PASS`, `FAIL <reason>`,
`BLOCKED <reason>`, or `MANUAL <pointer>`.

    python3 tests/dod_validate.py                # the close-out report
    python3 tests/dod_validate.py --self-test     # each check must detect the
                                                   # loss of its own evidence

Design record: docs/decisions/ADR-0025-dod-close-out-gate.md.

stdlib only. No network access except through `gh`/`git` subprocesses, and
only for the handful of conditions that name GitHub as their instrument.

BLOCKED vs FAIL (ADR-0025 decision 1): BLOCKED iff the INSTRUMENT itself did
not answer (git/gh/bash unreachable, a timeout, a malformed response from the
tool itself). FAIL iff the instrument answered and the answer does not
satisfy the condition -- including "the file the condition needs does not
exist" and "I have not built this check yet". A stub that returns PASS is the
defect this file exists to prevent; every check below is written to be
provably unable to do that (see --self-test and each check's break_evidence
registered at the bottom of this file).
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# D22 reuses ADR-0039's own structured-receipt parser read-only, rather than
# re-implementing an approximation of it: this is a correctness fix against
# an already-accepted ADR (ADR-0039, accepted at commit cb8d68fc), not new
# architecture, and reading an already-integrated public module strictly
# read-only does not modify its owning crew's (crew_alpha's) code path -- so
# neither a new ADR nor that crew's sign-off is needed here. Same import
# pattern already used by tests/test_review_receipts.py and
# tests/test_follow_up_queueing.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import nightshift_review_receipts as review_receipts  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
THIS_FILE = Path(__file__).resolve()
MANIFEST_REL = Path("docs/prd/DOD-MANIFEST.json")
DOD_REL = Path("docs/prd/DEFINITION-OF-DONE.md")
MANUAL_CONDITIONS = (8, 21)
FROZEN_PATHS = ("/srv/nightshift", "/home/nightshift/nightshift-runtime")
# B1's baseline commit (docs/prd/DEFINITION-OF-DONE.md) -- condition 22's own
# commit-range clause is measured from here, not from an arbitrary recent tip.
D22_BASE_SHA = "a73ba881c04a4e5e2b716f89a0beb08a6e1164dc"


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

class Status:
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    MANUAL = "MANUAL"
    ALL = (PASS, FAIL, BLOCKED, MANUAL)


class Verdict:
    __slots__ = ("status", "reason")

    def __init__(self, status: str, reason: str = ""):
        if status not in Status.ALL:
            raise ValueError(f"unknown verdict status {status!r}")
        if status != Status.PASS and not reason:
            raise ValueError(f"{status} requires a non-empty reason")
        self.status = status
        self.reason = reason

    def line(self, condition_id: str) -> str:
        if self.status == Status.PASS:
            return f"{condition_id} PASS" if not self.reason else f"{condition_id} PASS ({self.reason})"
        return f"{condition_id} {self.status} {self.reason}"

    def __repr__(self):
        return f"Verdict({self.status!r}, {self.reason!r})"


def verdict_pass(reason: str = "") -> Verdict:
    return Verdict(Status.PASS, reason)


def verdict_fail(reason: str) -> Verdict:
    return Verdict(Status.FAIL, reason)


def verdict_blocked(reason: str) -> Verdict:
    return Verdict(Status.BLOCKED, reason)


def verdict_manual(pointer: str) -> Verdict:
    return Verdict(Status.MANUAL, pointer)


class InstrumentUnavailable(Exception):
    """The OBSERVING FACILITY itself did not answer. Never raised because the
    answer was unwelcome -- that is always a Verdict(FAIL, ...) instead."""


class ManifestMissing(Exception):
    """docs/prd/DOD-MANIFEST.json is absent, unreadable, or not valid JSON."""


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------

class Ctx:
    __slots__ = ("repo_root", "manifest", "manifest_error")

    def __init__(self, repo_root: Path, manifest: Optional[dict], manifest_error: Optional[str]):
        self.repo_root = repo_root
        self.manifest = manifest
        self.manifest_error = manifest_error


def _need_manifest(ctx: Ctx) -> dict:
    if ctx.manifest is None:
        raise ManifestMissing(ctx.manifest_error or f"{MANIFEST_REL} not found")
    return ctx.manifest


def load_manifest(repo_root: Path):
    path = repo_root / MANIFEST_REL
    if not path.exists():
        return None, f"{MANIFEST_REL} not found"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"{MANIFEST_REL} unreadable: {e}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"{MANIFEST_REL} malformed JSON: {e}"
    if not isinstance(data, dict):
        return None, f"{MANIFEST_REL} must be a JSON object at the top level"
    return data, None


# --------------------------------------------------------------------------
# Instrument wrappers -- these raise InstrumentUnavailable, never return a
# FAIL-shaped result, when the instrument itself is the problem.
# --------------------------------------------------------------------------

def run_git(args, cwd=None, timeout=30):
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd or REPO_ROOT),
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise InstrumentUnavailable(f"git unavailable: {e}") from e


def run_gh(args, timeout=30):
    try:
        return subprocess.run(
            ["gh", *args], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise InstrumentUnavailable(f"gh unavailable: {e}") from e


def run_bash_n(script: Path):
    try:
        return subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise InstrumentUnavailable(f"bash unavailable: {e}") from e


# --------------------------------------------------------------------------
# Test-node execution -- a private --run-node mode, invoked as a subprocess,
# emits ONE json object with exact counts. Chosen over parsing human
# `unittest -v` output (ADR-0025 decision 4/option A) because condition 8
# needs an EXACT zero-skip assertion.
# --------------------------------------------------------------------------

def run_test_node(node_ids, cwd_choice: str, timeout: int = 180):
    """node_ids: a dotted unittest node id, or a list of them (run as one
    suite). cwd_choice: "root" (loader anchored at the repo root, node ids
    like "tests.test_go_toolchain.RT01...") or "tests" (loader anchored at
    tests/, node ids like "test_fencing" or "test_slots.Cls.method") --
    matching the two invocation forms actually used by this project's
    existing docs/audit/baseline/*.json baseline records.

    Returns a dict {testsRun, failures, errors, skipped, load_errors} or None
    if the runner itself could not be invoked (the caller must treat that as
    an instrument failure, i.e. raise InstrumentUnavailable, not FAIL).
    """
    if isinstance(node_ids, str):
        node_ids = [node_ids]
    payload = json.dumps({"node_ids": node_ids, "cwd_choice": cwd_choice})
    try:
        proc = subprocess.run(
            [sys.executable, str(THIS_FILE), "--run-node", payload],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _run_node_worker(payload_json: str) -> None:
    payload = json.loads(payload_json)
    node_ids = payload["node_ids"]
    cwd_choice = payload["cwd_choice"]
    base = REPO_ROOT if cwd_choice == "root" else REPO_ROOT / "tests"
    os.chdir(base)
    sys.path.insert(0, str(base))
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    load_errors = []
    for nid in node_ids:
        try:
            suite.addTests(loader.loadTestsFromName(nid))
        except Exception as e:  # noqa: BLE001 -- deliberately broad: a bad node id is data, not a crash
            load_errors.append(f"{nid}: {e!r}")
    result = unittest.TestResult()
    if not load_errors:
        suite.run(result)
    out = {
        "testsRun": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "load_errors": load_errors,
    }
    print(json.dumps(out))


def _test_run_green(node_ids, cwd_choice, label) -> Optional[Verdict]:
    """Shared shape used by every condition that just needs "this test node is
    green with zero skips". Returns None (meaning: keep going) on success, a
    FAIL Verdict on a bad result, and raises InstrumentUnavailable if the
    runner itself could not be reached."""
    result = run_test_node(node_ids, cwd_choice)
    if result is None:
        raise InstrumentUnavailable(f"{label}: unittest runner unavailable for {node_ids!r}")
    if result["load_errors"]:
        return verdict_fail(f"{label}: test node failed to load: {result['load_errors']}")
    if result["testsRun"] == 0:
        return verdict_fail(f"{label}: test node {node_ids!r} loaded zero tests")
    if result["failures"] or result["errors"]:
        return verdict_fail(
            f"{label}: {node_ids!r} not green -- failures={result['failures']} errors={result['errors']}"
        )
    if result["skipped"]:
        return verdict_fail(f"{label}: {node_ids!r} has {result['skipped']} skips (zero required)")
    return None


# --------------------------------------------------------------------------
# RT id / baseline record validation (condition 8's rules), reused by every
# RT-bound condition (1-7) and by condition 8's own manifest-wide sweep.
# --------------------------------------------------------------------------

def _rt_num(rt_id: str) -> int:
    m = re.match(r"RT-(\d+)", rt_id)
    return int(m.group(1)) if m else 0


def rt_entries_for_condition(manifest: dict, condition_n: int):
    out = []
    for rt_id, entry in (manifest.get("rt_ids") or {}).items():
        if isinstance(entry, dict) and entry.get("condition") == condition_n:
            out.append((rt_id, entry))
    out.sort(key=lambda kv: _rt_num(kv[0]))
    return out


def baseline_kind(entry: dict) -> Optional[str]:
    baseline = entry.get("baseline") or {}
    exit_code = baseline.get("exit_code")
    if not isinstance(exit_code, int):
        return None
    return "regression" if exit_code != 0 else "preservation"


def entry_output_path(repo_root: Path, rel: str) -> Optional[Path]:
    """A path INSIDE docs/audit/baseline/, or None if it would escape it."""
    try:
        p = (repo_root / rel).resolve()
        baseline_dir = (repo_root / "docs" / "audit" / "baseline").resolve()
        p.relative_to(baseline_dir)
    except (ValueError, OSError):
        return None
    return p


_PATCH_TOUCHED_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def _validate_mutation(repo_root, rt_id, idx, mut, baseline_command, bound_file):
    label = f"{rt_id}.mutations[{idx}]"
    if not isinstance(mut, dict):
        return f"{label}: not an object"
    for field in ("patch_path", "applied_to_sha", "command", "exit_code", "output_path"):
        if field not in mut:
            return f"{label}: missing '{field}'"
    if mut["command"] != baseline_command:
        return f"{label}: command is not byte-identical to the baseline command"
    if not isinstance(mut["exit_code"], int) or mut["exit_code"] == 0:
        return f"{label}: exit_code must be a non-zero integer"
    out = entry_output_path(repo_root, mut["output_path"])
    if out is None or not out.exists() or out.stat().st_size == 0:
        return f"{label}: output_path missing, empty, or outside docs/audit/baseline/"
    patch_path = entry_output_path(repo_root, mut["patch_path"])
    if patch_path is None or not patch_path.exists():
        return f"{label}: patch_path missing or outside docs/audit/baseline/"
    sha = mut["applied_to_sha"]
    anc = run_git(["merge-base", "--is-ancestor", str(sha), "HEAD"], cwd=repo_root)
    if anc.returncode not in (0, 1):
        return f"{label}: git merge-base --is-ancestor errored: {anc.stderr.strip()[:200]}"
    if anc.returncode != 0:
        return f"{label}: applied_to_sha {sha} is not an ancestor of HEAD"
    try:
        patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"{label}: patch unreadable: {e}"
    touched = set(_PATCH_TOUCHED_RE.findall(patch_text))
    if not touched:
        return f"{label}: patch declares no touched files (cannot verify the subset rule)"
    if not touched.issubset({bound_file}):
        return f"{label}: patch touches {sorted(touched - {bound_file})}, outside the RT id's declared production file {bound_file!r}"
    return None


def validate_rt_baseline(repo_root: Path, rt_id: str, entry) -> Optional[str]:
    """Returns None if the manifest entry is structurally valid (which an
    UNSATISFIED entry can be), else a violation string. Never runs the test
    itself -- that is the caller's job, once it knows the entry resolves."""
    if not isinstance(entry, dict):
        return f"{rt_id}: manifest entry is not an object"
    condition = entry.get("condition")
    if not isinstance(condition, int) or not (1 <= condition <= 24):
        return f"{rt_id}: missing/invalid 'condition' (must be an int 1..24)"
    node = entry.get("test_node_id")
    if node is None:
        if not entry.get("unsatisfied_reason"):
            return f"{rt_id}: unsatisfied but carries no unsatisfied_reason"
        return None
    baseline = entry.get("baseline")
    if not isinstance(baseline, dict):
        return f"{rt_id}: satisfied (test_node_id set) but has no baseline record"
    for field in ("baseline_sha", "command", "exit_code", "output_path"):
        if field not in baseline:
            return f"{rt_id}: baseline record missing '{field}'"
    exit_code = baseline["exit_code"]
    if not isinstance(exit_code, int):
        return f"{rt_id}: baseline exit_code must be an integer"
    derived_kind = "regression" if exit_code != 0 else "preservation"
    declared_kind = baseline.get("kind")
    if declared_kind is not None and declared_kind != derived_kind:
        return f"{rt_id}: declared kind {declared_kind!r} disagrees with derived kind {derived_kind!r} (exit_code={exit_code})"
    output_path = entry_output_path(repo_root, baseline["output_path"])
    if output_path is None:
        return f"{rt_id}: baseline output_path {baseline['output_path']!r} is outside docs/audit/baseline/"
    if not output_path.exists() or output_path.stat().st_size == 0:
        return f"{rt_id}: baseline output_path {baseline['output_path']} missing or empty"
    sha = baseline["baseline_sha"]
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return f"{rt_id}: baseline_sha {sha!r} is not a plausible git sha"
    anc = run_git(["merge-base", "--is-ancestor", sha, "HEAD"], cwd=repo_root)
    if anc.returncode not in (0, 1):
        return f"{rt_id}: git merge-base --is-ancestor errored: {anc.stderr.strip()[:200]}"
    if anc.returncode != 0:
        return f"{rt_id}: baseline_sha {sha} is not an ancestor of HEAD"
    binding = entry.get("binding")
    if not isinstance(binding, dict) or not binding.get("file"):
        return f"{rt_id}: missing 'binding.file'"
    bound_file = binding["file"]
    if not (repo_root / bound_file).exists():
        return f"{rt_id}: binding.file {bound_file} does not exist"
    if derived_kind == "preservation":
        mutations = baseline.get("mutations")
        if isinstance(mutations, dict):
            mutations = [mutations]
        if not mutations:
            return f"{rt_id}: preservation record has no mutations array"
        for i, mut in enumerate(mutations):
            err = _validate_mutation(repo_root, rt_id, i, mut, baseline["command"], bound_file)
            if err:
                return err
    return None


# --------------------------------------------------------------------------
# Generic RT-bound condition (conditions 1-7): every declared RT id must be
# structurally valid, resolve to a real test node, and that node must be
# green with zero skips. Any extra structural checks are composed in.
# --------------------------------------------------------------------------

def generic_rt_condition(condition_n: int, structural=()):
    def _run(ctx: Ctx) -> Verdict:
        manifest = _need_manifest(ctx)
        entries = rt_entries_for_condition(manifest, condition_n)
        if not entries:
            return verdict_fail(f"no RT ids declared for condition {condition_n} in the manifest")
        kinds = []
        for rt_id, entry in entries:
            err = validate_rt_baseline(ctx.repo_root, rt_id, entry)
            if err:
                return verdict_fail(err)
            if entry.get("test_node_id") is None:
                return verdict_fail(f"{rt_id}: unsatisfied ({entry.get('unsatisfied_reason')})")
            v = _test_run_green(entry["test_node_id"], entry.get("cwd", "root"), rt_id)
            if v is not None:
                return v
            kinds.append(baseline_kind(entry))
        if condition_n <= 7 and kinds and set(kinds) == {"preservation"}:
            return verdict_fail(f"condition {condition_n} would be evidenced by preservation records alone")
        for fn in structural:
            v = fn(ctx.repo_root)
            if v is not None:
                return v
        return verdict_pass()
    return _run


def break_generic(condition_n: int):
    def _break(fixture_root: Path, manifest):
        m = copy.deepcopy(manifest) if manifest else {"rt_ids": {}}
        for rt_id, entry in (m.get("rt_ids") or {}).items():
            if isinstance(entry, dict) and entry.get("condition") == condition_n:
                baseline = entry.get("baseline") or {}
                out = baseline.get("output_path")
                if out:
                    p = entry_output_path(fixture_root, out)
                    if p and p.exists():
                        p.unlink()
                entry["test_node_id"] = None
                entry["baseline"] = None
                entry["unsatisfied_reason"] = "self-test: evidence deliberately removed"
        return m
    return _break


def _bash_n_check(rel_path: str):
    def _check(repo_root: Path) -> Optional[Verdict]:
        script = repo_root / rel_path
        if not script.exists():
            return verdict_fail(f"{rel_path} not found")
        proc = run_bash_n(script)
        if proc.returncode != 0:
            return verdict_fail(f"bash -n {rel_path} failed: {proc.stderr.strip()[:200]}")
        return None
    return _check


def _grep_absent_check(rel_path: str, needle: str):
    def _check(repo_root: Path) -> Optional[Verdict]:
        p = repo_root / rel_path
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8", errors="replace")
        if needle in text:
            return verdict_fail(f"{rel_path} still contains the false claim {needle!r}")
        return None
    return _check


SIX_UNITS = (
    "omnigent-server.service", "omnigent-host.service",
    "nightshift-state-backup.service", "nightshift-state-backup.timer",
    "nightshift-watchdog.service", "nightshift-watchdog.timer",
)


def _six_units_named_check(repo_root: Path) -> Optional[Verdict]:
    p = repo_root / "tests" / "test_user_manager.py"
    if not p.exists():
        return verdict_fail("tests/test_user_manager.py not found")
    text = p.read_text(encoding="utf-8", errors="replace")
    missing = [u for u in SIX_UNITS if u not in text]
    if missing:
        return verdict_fail(f"tests/test_user_manager.py does not name unit(s): {missing}")
    return None


# --------------------------------------------------------------------------
# Condition 8 -- the manifest itself
# --------------------------------------------------------------------------

def check_d08(ctx: Ctx) -> Verdict:
    manifest = _need_manifest(ctx)
    dod_path = ctx.repo_root / DOD_REL
    if not dod_path.exists():
        return verdict_fail(f"{DOD_REL} not found")
    dod_text = dod_path.read_text(encoding="utf-8", errors="replace")
    referenced = sorted(set(re.findall(r"RT-\d+", dod_text)), key=_rt_num)
    rt_ids = manifest.get("rt_ids") or {}
    missing = [r for r in referenced if r not in rt_ids]
    if missing:
        return verdict_fail(f"RT ids referenced in the DoD but absent from the manifest: {missing}")

    violations = []
    for rt_id in sorted(rt_ids, key=_rt_num):
        err = validate_rt_baseline(ctx.repo_root, rt_id, rt_ids[rt_id])
        if err:
            violations.append(err)
    if violations:
        shown = violations[:5]
        more = f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""
        return verdict_fail("; ".join(shown) + more)

    for rt_id, entry in rt_ids.items():
        if entry.get("test_node_id") is None:
            continue
        v = _test_run_green(entry["test_node_id"], entry.get("cwd", "root"), rt_id)
        if v is not None:
            return v

    for cond in range(1, 8):
        entries = rt_entries_for_condition(manifest, cond)
        satisfied = [(r, e) for r, e in entries if e.get("test_node_id") is not None]
        if satisfied and all(baseline_kind(e) == "preservation" for _, e in satisfied):
            return verdict_fail(f"condition {cond} would be evidenced by preservation records alone")

    cond8 = (manifest.get("conditions") or {}).get("8") or {}
    reviewed_by = cond8.get("reviewed_by")
    if not reviewed_by:
        return verdict_fail(
            "manifest is structurally valid, but conditions.8.reviewed_by is not populated "
            "-- the manual exception has not been claimed by a cross-crew reviewer yet"
        )
    return verdict_manual(str(reviewed_by))


def break_d08(fixture_root: Path, manifest):
    m = copy.deepcopy(manifest) if manifest else {"rt_ids": {}}
    rt = (m.get("rt_ids") or {}).get("RT-01")
    if rt is not None:
        rt["test_node_id"] = None
        rt["baseline"] = None
        # deliberately NO unsatisfied_reason: this is the exact shape decision
        # 3's binding invariant exists to catch -- an entry that looks broken
        # in a way validate_rt_baseline itself flags.
    return m


# --------------------------------------------------------------------------
# Condition 9 -- the validator's own registry
# --------------------------------------------------------------------------

def check_d09(ctx: Ctx) -> Verdict:
    manifest = _need_manifest(ctx)
    conditions = manifest.get("conditions") or {}
    bad = []
    for n in range(1, 25):
        kind = (conditions.get(str(n)) or {}).get("kind")
        expected = "manual" if n in MANUAL_CONDITIONS else "auto"
        if kind != expected:
            bad.append(f"condition {n} declares kind={kind!r}, expected {expected!r}")
    if bad:
        return verdict_fail("; ".join(bad))
    if len(REGISTRY) != 24:
        return verdict_fail(f"registry has {len(REGISTRY)} checks registered, expected exactly 24")
    ids = [c.id for c in REGISTRY]
    expected_ids = [f"D{n:02d}" for n in range(1, 25)]
    if ids != expected_ids:
        return verdict_fail(f"registry ids are out of order or incomplete: {ids}")
    for c in REGISTRY:
        if not callable(c.run) or not callable(c.break_evidence):
            return verdict_fail(f"{c.id}: missing a real run or break_evidence callable")
    cond21 = conditions.get("21") or {}
    if "frozen_path_control" not in cond21:
        return verdict_fail("conditions.21 declares no 'frozen_path_control' field")
    return verdict_pass()


def break_d09(fixture_root: Path, manifest):
    m = copy.deepcopy(manifest) if manifest else {"conditions": {}}
    conds = m.setdefault("conditions", {})
    conds.setdefault("8", {})["kind"] = "auto"
    return m


# --------------------------------------------------------------------------
# Condition 10 -- CI
# --------------------------------------------------------------------------

def check_d10(ctx: Ctx) -> Verdict:
    workflow_path = ctx.repo_root / ".github" / "workflows" / "tests.yml"
    if not workflow_path.exists():
        return verdict_fail(".github/workflows/tests.yml not found")
    text = workflow_path.read_text(encoding="utf-8", errors="replace")
    on_match = re.search(r"^on:\n((?:[ \t]+.*\n?)+)", text, re.MULTILINE)
    if not on_match:
        return verdict_fail("tests.yml has no top-level 'on:' block")
    on_keys = set(re.findall(r"^\s{2}([A-Za-z_]+):", on_match.group(1), re.MULTILINE))
    if "pull_request" not in on_keys:
        return verdict_fail("tests.yml's 'on:' block does not trigger on pull_request")
    if "push" not in on_keys:
        return verdict_fail("tests.yml's 'on:' block does not trigger on push")

    manifest = _need_manifest(ctx)
    required_jobs = manifest.get("required_ci_jobs")
    if not required_jobs:
        return verdict_fail("manifest declares no required_ci_jobs")
    jobs_block = text.split("\njobs:", 1)
    job_names = set(re.findall(r"^\s{2}([A-Za-z0-9_.-]+):\n", jobs_block[1], re.MULTILINE)) if len(jobs_block) > 1 else set()
    absent = [j for j in required_jobs if j not in job_names]
    if absent:
        return verdict_fail(f"required_ci_jobs not found as jobs in tests.yml: {absent}")

    # The repository is read from the checkout, never written down here: a
    # hardcoded owner/repo makes this check answer about somebody else's
    # repository the moment the project is forked or renamed.
    nwo = run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if nwo.returncode != 0:
        raise InstrumentUnavailable("gh repo view failed: cannot resolve this repository")
    commits = run_gh(["api", f"repos/{nwo.stdout.strip()}/commits/main", "--jq", ".sha"])
    if commits.returncode != 0:
        raise InstrumentUnavailable(f"gh api commits/main failed: {commits.stderr.strip()[:200]}")
    main_sha = commits.stdout.strip()

    runs = run_gh([
        "run", "list", "--workflow", "tests.yml", "--branch", "main", "--limit", "1",
        "--json", "headSha,conclusion,databaseId",
    ])
    if runs.returncode != 0:
        raise InstrumentUnavailable(f"gh run list failed: {runs.stderr.strip()[:200]}")
    try:
        run_rows = json.loads(runs.stdout)
    except json.JSONDecodeError:
        raise InstrumentUnavailable("gh run list returned non-JSON output")
    if not run_rows:
        return verdict_fail("no completed tests.yml run found on main")
    row = run_rows[0]
    if row.get("headSha") != main_sha:
        return verdict_fail(f"most recent tests.yml run on main is at {row.get('headSha')}, not the current tip {main_sha}")
    if row.get("conclusion") != "success":
        return verdict_fail(f"most recent tests.yml run on main concluded {row.get('conclusion')!r}, not success")

    jobs = run_gh(["run", "view", str(row.get("databaseId")), "--json", "jobs", "--jq", "[.jobs[]|{name,conclusion}]"])
    if jobs.returncode != 0:
        raise InstrumentUnavailable(f"gh run view failed: {jobs.stderr.strip()[:200]}")
    try:
        job_rows = json.loads(jobs.stdout)
    except json.JSONDecodeError:
        raise InstrumentUnavailable("gh run view returned non-JSON output")
    job_conclusions = {j["name"]: j["conclusion"] for j in job_rows}
    for jn in required_jobs:
        if job_conclusions.get(jn) != "success":
            return verdict_fail(f"required CI job {jn!r} concluded {job_conclusions.get(jn)!r}, not success")
    return verdict_pass()


def break_d10(fixture_root: Path, manifest):
    p = fixture_root / ".github" / "workflows" / "tests.yml"
    if p.exists():
        text = p.read_text(encoding="utf-8")
        text = text.replace("pull_request:\n", "").replace("pull_request:", "")
        p.write_text(text, encoding="utf-8")
    return copy.deepcopy(manifest) if manifest else manifest


# --------------------------------------------------------------------------
# Conditions 11-12 -- seam B's M2 items. This validator checks them for real;
# it does not build them (out of scope, MILESTONES.md seam split).
# --------------------------------------------------------------------------

def check_d11(ctx: Ctx) -> Verdict:
    script = ctx.repo_root / "tests" / "linux" / "install-rehearsal.sh"
    if not script.exists():
        return verdict_fail("tests/linux/install-rehearsal.sh not found")
    text = script.read_text(encoding="utf-8", errors="replace").lower()
    if "hermetic" not in text or "networked" not in text or "stub" not in text:
        return verdict_fail(
            "tests/linux/install-rehearsal.sh header does not declare hermetic/networked/stubbed per step"
        )
    manifest = _need_manifest(ctx)
    required_jobs = manifest.get("required_ci_jobs") or []
    rehearsal_job = manifest.get("rehearsal_ci_job")
    if not rehearsal_job or rehearsal_job not in required_jobs:
        return verdict_fail("manifest does not name a rehearsal_ci_job that is also in required_ci_jobs")
    return verdict_pass()


def break_d11(fixture_root: Path, manifest):
    p = fixture_root / "tests" / "linux" / "install-rehearsal.sh"
    if p.exists():
        p.unlink()
    m = copy.deepcopy(manifest) if manifest else {}
    m.pop("rehearsal_ci_job", None)
    return m


def _preflight_check_messages(text: str) -> set:
    """Every literal check message `deploy/preflight.sh` reports.

    Matched at the start of a statement -- line start, or after `;`, `&&`,
    `||`, `then`, `else`, `do` -- rather than anywhere in the line. An earlier
    version matched anywhere, so `# bad "a retired message"` in a comment and
    the word `bad` inside an unrelated echo both demanded catalogue entries
    that could not sensibly exist.

    Single-quoted messages are matched too. Accepting only double quotes left
    an open bypass: a new check written with single quotes was invisible to
    this direction entirely.

    Skipped, because they have no fixed text to catalogue and the forward
    direction still covers them:
      - messages built by command substitution (`$(...)` or backticks);
      - messages that re-open a quote mid-string (`"no config at "$path" here"`),
        which cannot be compared literally and would truncate.

    Measured against the real script when written: 80 `bad`/`warn` call sites,
    76 of which this returns and every one of those is catalogued. The four it
    does not return are the command-substituted one (`preflight.sh:188`) and
    three that re-open a quote mid-message. Those keep only the forward
    direction's cover, which is a real gap and is stated rather than rounded
    away -- a reverse check that claimed 80 while testing 76 would be the kind
    of number this epic exists to distrust.
    """
    import re as _re
    lead = r'(?:^|[;&|]|\bthen\b|\belse\b|\bdo\b)\s*'
    pattern = _re.compile(
        lead + r'(?:bad|warn)\s+(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')',
        _re.M)
    found = set()
    for match in pattern.finditer(text):
        message = match.group(1) if match.group(1) is not None else match.group(2)
        if "$(" in message or "`" in message:
            continue
        found.add(message)
    return found


def check_d12(ctx: Ctx) -> Verdict:
    manifest = _need_manifest(ctx)
    section = manifest.get("preflight_checks")
    if not section:
        return verdict_fail("manifest declares no preflight_checks section")
    script = ctx.repo_root / "deploy" / "preflight.sh"
    if not script.exists():
        return verdict_fail("deploy/preflight.sh not found")
    text = script.read_text(encoding="utf-8", errors="replace")
    for entry in section:
        if not isinstance(entry, dict) or not all(k in entry for k in ("id", "severity", "remediation")):
            return verdict_fail(f"malformed preflight_checks entry: {entry!r}")
        if entry["severity"] not in ("FAIL", "warn"):
            return verdict_fail(f"{entry['id']}: severity must be 'FAIL' or 'warn'")
        if entry["id"] not in text:
            return verdict_fail(f"{entry['id']}: not found in deploy/preflight.sh")
    # Issue #729 / ADR-0093: the other direction. Checking only that every
    # catalogued entry exists in the script lets a NEW check be added to
    # preflight.sh with no catalogue entry, silently, while this verdict stays
    # PASS -- the catalogue drifts behind the thing it is supposed to describe
    # and nobody is told. #716 change 4 is precisely about a check that exists
    # and is not accounted for.
    catalogued = {entry["id"] for entry in section}
    for message in _preflight_check_messages(text):
        if message not in catalogued:
            return verdict_fail(
                f"{message!r}: a check in deploy/preflight.sh with no "
                "preflight_checks entry in DOD-MANIFEST.json -- catalogue it, "
                "so the manifest keeps describing the script it claims to")
    proc = run_bash_n(script)
    if proc.returncode != 0:
        return verdict_fail(f"bash -n deploy/preflight.sh failed: {proc.stderr.strip()[:200]}")
    return verdict_pass()


def break_d12(fixture_root: Path, manifest):
    m = copy.deepcopy(manifest) if manifest else {}
    m.pop("preflight_checks", None)
    return m


# --------------------------------------------------------------------------
# Conditions 13-18 -- the audits (Section D's uniform Qn-record format)
# --------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
_ALLOWED_VERDICTS = {"CONFIRMED-BUG", "SOUND", "ACCEPTED-RISK", "OUT-OF-SCOPE"}
_ALLOWED_EVIDENCE_TYPES = {"permalink", "command", "test"}


def _parse_json_blocks(doc_path: Path):
    if not doc_path.exists():
        return None, f"{doc_path.name} not found"
    text = doc_path.read_text(encoding="utf-8", errors="replace")
    blocks = _JSON_BLOCK_RE.findall(text)
    if not blocks:
        return None, f"{doc_path.name} has no fenced ```json record"
    records = []
    for b in blocks:
        try:
            obj = json.loads(b)
        except json.JSONDecodeError as e:
            return None, f"{doc_path.name}: malformed json record: {e}"
        records.extend(obj if isinstance(obj, list) else [obj])
    return records, None


def _validate_qa_records(records, expected_q_count: int) -> Optional[str]:
    ids_seen = []
    for r in records:
        if not isinstance(r, dict):
            return f"a record is not an object: {r!r}"
        rid = r.get("id")
        if not rid or not re.fullmatch(r"Q\d+", str(rid)):
            return f"record has a missing/invalid id: {r!r}"
        ids_seen.append(str(rid))
        if r.get("verdict") not in _ALLOWED_VERDICTS:
            return f"{rid}: verdict {r.get('verdict')!r} not in {sorted(_ALLOWED_VERDICTS)}"
        evidence = r.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return f"{rid}: evidence must be a non-empty array"
        for ev in evidence:
            if not isinstance(ev, dict) or ev.get("type") not in _ALLOWED_EVIDENCE_TYPES:
                return f"{rid}: an evidence item has an invalid/missing type: {ev!r}"
        findings = r.get("findings")
        if findings is not None and not isinstance(findings, list):
            return f"{rid}: findings must be a list if present"
    expected = [f"Q{n}" for n in range(1, expected_q_count + 1)]
    if sorted(ids_seen, key=lambda s: int(s[1:])) != expected:
        missing = sorted(set(expected) - set(ids_seen), key=lambda s: int(s[1:]))
        extra = sorted(set(ids_seen) - set(expected))
        dupes = sorted({x for x in ids_seen if ids_seen.count(x) > 1})
        return f"question ids don't match Q1..Q{expected_q_count} exactly (missing={missing}, extra={extra}, duplicates={dupes})"
    return None


def audit_doc_check(rel_path: str, expected_q_count: int):
    def _run(ctx: Ctx) -> Verdict:
        doc_path = ctx.repo_root / rel_path
        records, err = _parse_json_blocks(doc_path)
        if err:
            return verdict_fail(err)
        v_err = _validate_qa_records(records, expected_q_count)
        if v_err:
            return verdict_fail(v_err)
        return verdict_pass()
    return _run


def break_doc(rel_path: str):
    def _break(fixture_root: Path, manifest):
        p = fixture_root / rel_path
        if p.exists():
            p.unlink()
        return copy.deepcopy(manifest) if manifest else manifest
    return _break


def check_d17(ctx: Ctx) -> Verdict:
    v = audit_doc_check("docs/audit/INSTALL.md", 4)(ctx)
    if v.status != Status.PASS:
        return v
    text = (ctx.repo_root / "docs" / "audit" / "INSTALL.md").read_text(encoding="utf-8", errors="replace")
    if "## See it for yourself" not in text:
        return verdict_fail("docs/audit/INSTALL.md has no '## See it for yourself' section")
    return verdict_pass()


def _audit_doc_finding_ids(repo_root: Path, rel_path: str):
    """Every F-nnn cited in an audit document's OWN per-question records (the
    'findings' array inside each Q record), or None if the document itself
    does not exist or does not parse. Used to confirm a FINDINGS.md id is
    'resolvable from the audit documents' (condition 18), not merely a
    self-consistent id that FINDINGS.md invented on its own."""
    records, err = _parse_json_blocks(repo_root / rel_path)
    if err:
        return None
    ids = set()
    for r in records:
        if isinstance(r, dict):
            for f in (r.get("findings") or []):
                ids.add(str(f))
    return ids


def check_d18(ctx: Ctx) -> Verdict:
    path = ctx.repo_root / "docs" / "audit" / "FINDINGS.md"
    records, err = _parse_json_blocks(path)
    if err:
        return verdict_fail(err)
    ids = [r.get("id") for r in records if isinstance(r, dict)]
    if len(ids) != len(set(ids)):
        return verdict_fail("duplicate finding ids in docs/audit/FINDINGS.md")
    manifest = None
    for r in records:
        if not isinstance(r, dict):
            return verdict_fail(f"a finding record is not an object: {r!r}")
        fid = r.get("id")
        if not fid or not re.fullmatch(r"F-\d+", str(fid)):
            return verdict_fail(f"finding has a missing/invalid id: {r!r}")
        source_doc = r.get("source_doc")
        if not source_doc:
            return verdict_fail(f"{fid}: missing source_doc")
        doc_ids = _audit_doc_finding_ids(ctx.repo_root, source_doc)
        if doc_ids is None:
            return verdict_fail(f"{fid}: source_doc {source_doc!r} does not exist or has no parseable json record -- id not resolvable")
        if str(fid) not in doc_ids:
            return verdict_fail(f"{fid}: not cited in {source_doc}'s own findings index -- id is not resolvable from the audit documents")
        verdict_v = r.get("verdict")
        if verdict_v not in _ALLOWED_VERDICTS:
            return verdict_fail(f"{fid}: verdict {verdict_v!r} not in {sorted(_ALLOWED_VERDICTS)}")
        disposition = r.get("disposition")
        if not disposition:
            return verdict_fail(f"{fid}: empty disposition")
        disposition_text = str(disposition)
        if verdict_v == "CONFIRMED-BUG":
            pr_match = re.search(r"PR\s*#?(\d+)", disposition_text)
            rt_match = re.search(r"RT-\d+", disposition_text)
            if not (pr_match and rt_match):
                return verdict_fail(f"{fid}: CONFIRMED-BUG disposition must name an integrated PR and an RT id: {disposition_text!r}")
            rt_id = rt_match.group(0)
            manifest = manifest if manifest is not None else _need_manifest(ctx)
            rt_entry = (manifest.get("rt_ids") or {}).get(rt_id)
            if rt_entry is None:
                return verdict_fail(f"{fid}: disposition cites {rt_id}, which is not a real RT id in {MANIFEST_REL}")
            baseline_err = validate_rt_baseline(ctx.repo_root, rt_id, rt_entry)
            if baseline_err:
                return verdict_fail(f"{fid}: {rt_id} does not resolve to a valid baseline record: {baseline_err}")
            pr_n = int(pr_match.group(1))
            change_prs = {c["pr"] if isinstance(c, dict) else c for c in (manifest.get("changes") or [])}
            if pr_n not in change_prs:
                return verdict_fail(f"{fid}: disposition cites PR #{pr_n}, which is not tracked in the manifest's 'changes' list -- condition 22 cannot confirm it followed the workflow (merged or gate-queued)")
        if "tech-debt.md" in disposition_text and verdict_v != "ACCEPTED-RISK":
            return verdict_fail(f"{fid}: only ACCEPTED-RISK may point to .omni/tech-debt.md")
        if verdict_v == "ACCEPTED-RISK" and "tech-debt.md" in disposition_text:
            if not re.search(r"trigger", disposition_text, re.IGNORECASE) or not re.search(r"owner", disposition_text, re.IGNORECASE):
                return verdict_fail(f"{fid}: ACCEPTED-RISK disposition must name a trigger and an owner")
    return verdict_pass()


# --------------------------------------------------------------------------
# Conditions 19-20 -- non-regression capability/credential coverage
# --------------------------------------------------------------------------

def check_d19(ctx: Ctx) -> Verdict:
    manifest = _need_manifest(ctx)
    caps = manifest.get("protected_capabilities")
    if not caps:
        return verdict_fail("manifest declares no protected_capabilities")
    floor = manifest.get("protected_capabilities_count_at_v2_adoption")
    if not isinstance(floor, int):
        return verdict_fail("manifest is missing protected_capabilities_count_at_v2_adoption")
    if len(caps) < floor:
        return verdict_fail(f"protected_capabilities has {len(caps)} entries, fewer than the {floor} recorded at v2 adoption")
    for cap in caps:
        if not isinstance(cap, dict) or not cap.get("id") or not cap.get("denial_test_node"):
            return verdict_fail(f"malformed protected_capabilities entry: {cap!r}")
        v = _test_run_green(cap["denial_test_node"], cap.get("cwd", "root"), cap["id"])
        if v is not None:
            return v
    return verdict_pass()


def break_d19(fixture_root: Path, manifest):
    m = copy.deepcopy(manifest) if manifest else {}
    m.pop("protected_capabilities", None)
    return m


_SINK_ID_TABLE_RE = re.compile(r"^\|\s*`(SINK-\d+)`\s*\|", re.MULTILINE)


def _boundaries_sink_ids(ctx: Ctx):
    """The stable SINK-nn ids condition 16 Q1 enumerated in its own
    definitions table, or None if the table isn't there to compare against."""
    path = ctx.repo_root / "docs" / "audit" / "BOUNDARIES.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    ids = set(_SINK_ID_TABLE_RE.findall(text))
    return ids or None


def check_d20(ctx: Ctx) -> Verdict:
    manifest = _need_manifest(ctx)
    node = manifest.get("sentinel_test_node")
    if not node:
        return verdict_fail("manifest declares no sentinel_test_node (depends on condition 16's sink corpus, not yet enumerated)")
    declared = manifest.get("sentinel_sinks")
    if not declared:
        return verdict_fail("manifest declares no sentinel_sinks -- cannot confirm the sentinel test's scanned sink corpus is the one condition 16 Q1 enumerated")
    audit_ids = _boundaries_sink_ids(ctx)
    if not audit_ids:
        return verdict_fail("docs/audit/BOUNDARIES.md has no SINK-nn definitions table to compare sentinel_sinks against")
    declared_set = set(declared)
    if declared_set != audit_ids:
        missing = sorted(audit_ids - declared_set)
        extra = sorted(declared_set - audit_ids)
        return verdict_fail(f"sentinel_sinks does not match condition 16 Q1's sink corpus (missing={missing}, extra={extra})")
    v = _test_run_green(node, manifest.get("sentinel_test_cwd", "root"), "D20")
    if v is not None:
        return v
    return verdict_pass()


def break_d20(fixture_root: Path, manifest):
    m = copy.deepcopy(manifest) if manifest else {}
    m.pop("sentinel_test_node", None)
    return m


# --------------------------------------------------------------------------
# Condition 21 -- frozen paths
# --------------------------------------------------------------------------

def check_d21(ctx: Ctx) -> Verdict:
    log = run_git(["log", "--name-only", "--all", "--pretty=format:"], cwd=ctx.repo_root)
    if log.returncode != 0:
        return verdict_fail(f"git log --name-only --all failed: {log.stderr.strip()[:200]}")
    touched = {ln.strip() for ln in log.stdout.splitlines() if ln.strip()}
    bad = [p for p in touched if p.startswith("srv/nightshift") or p.startswith("home/nightshift/nightshift-runtime")]
    if bad:
        return verdict_fail(f"git history contains committed paths under a frozen root: {bad[:5]}")

    # NOTE on scope, deliberately narrowed rather than silently widened: the
    # DoD's own second clause here ("no committed script... writes to either
    # path, shebang-manifest scan for both literals in a write position") is,
    # read literally, unsatisfiable for THIS repository -- deploy/bootstrap.sh,
    # provision-ubuntu.sh and preflight.sh legitimately `install -d`/write to
    # /srv/nightshift as their designed install target on a FRESH box (measured:
    # `TEAMS_DIR="${TEAMS_DIR:-/srv/nightshift}"`, `install -d ... /srv/nightshift`).
    # That is not this project reaching into a frozen, already-running instance;
    # it is the deploy tooling's normal job. A literal grep-for-the-string check
    # would therefore permanently FAIL this condition for reasons unrelated to
    # the property condition 21 actually cares about (this repo's own git
    # history/tooling not reaching into the ALREADY-RUNNING frozen instance),
    # which is why the DoD names a MANUAL exception here in the first place
    # ("a forward-looking control ... supplied by the environment"). Reported
    # rather than silently resolved: `product` should clarify this clause in a
    # changelog row rather than have it stay a static-grep trap. This check
    # therefore verifies only the clause it CAN verify soundly -- the git-log
    # structural claim above (no tracked file living AT a path under either
    # frozen root) -- and leaves the write-position claim to the manual control.

    handover = ctx.repo_root / "docs" / "HANDOVER.md"
    if not handover.exists():
        return verdict_fail("docs/HANDOVER.md not found (must state neither frozen path was modified)")
    handover_text = handover.read_text(encoding="utf-8", errors="replace")
    if FROZEN_PATHS[0] not in handover_text or FROZEN_PATHS[1] not in handover_text:
        return verdict_fail("docs/HANDOVER.md does not mention both frozen paths")

    manifest = _need_manifest(ctx)
    control = (manifest.get("conditions") or {}).get("21", {}).get("frozen_path_control")
    if not control:
        return verdict_fail("manifest conditions.21.frozen_path_control is not populated -- manual exception not yet claimed")
    evidence_path = ctx.repo_root / control if isinstance(control, str) else None
    if evidence_path is None or not evidence_path.exists():
        return verdict_fail(f"conditions.21.frozen_path_control evidence file not found: {control!r}")
    return verdict_manual(str(control))


def break_d21(fixture_root: Path, manifest):
    m = copy.deepcopy(manifest) if manifest else {"conditions": {}}
    conds = m.setdefault("conditions", {})
    conds.setdefault("21", {})["frozen_path_control"] = None
    return m


# --------------------------------------------------------------------------
# Condition 22 -- workflow followed
# --------------------------------------------------------------------------

# bin/nightshift_review_receipts.py -- the module implementing ADR-0039's
# actual structured-receipt mechanism -- landed at commit `cb8d68fc`,
# 2026-08-28T09:50:53Z (`git log --diff-filter=A -- bin/nightshift_review_
# receipts.py`; the same commit also adds docs/decisions/ADR-0039-persist-
# review-receipts.md, so the ADR's acceptance and the tool's landing are the
# same event). A PR merged AFTER that exact timestamp was authored under its
# regime and must clear the ACTUAL mechanism -- a fenced `nightshift-review`
# JSON block that the module's own structural parser accepts, not a
# look-alike prose comment (this is a correctness fix reusing an already-
# accepted, already-integrated public module strictly read-only -- it does
# not modify crew_alpha's owned code path, so it needed no new ADR and no
# crew_alpha sign-off; recorded self-contained here rather than only in an
# uncommitted operational ledger a fresh clone of this diff cannot see). A
# PR merged AT OR BEFORE that timestamp is grandfathered to the weaker
# evidence bar -- the strict mechanism could not possibly have existed yet
# when it merged, and retroactively demanding it would make D22 permanently
# unpassable for historical merges through no fault of the PRs involved.
# **This must compare full timestamps, not truncated calendar dates**: the
# module landed mid-day (09:50:53Z) on 2026-08-28, so several real PRs
# (#234 at 07:13:31Z among them) merged EARLIER THE SAME CALENDAR DAY, before
# the tool existed at all -- a date-only comparison wrongly ungrandfathers
# them and FAILs D22 for lacking a mechanism that could not have existed for
# them yet (this exact defect, caught in round-3 review of this file: a
# `merged_at[:10] < "2026-08-28"` comparison passed both this file's own
# 8/20 and 9/01 boundary tests while still being wrong for any same-day,
# pre-tool merge -- fixed by comparing full ISO-8601 timestamps instead).
# Reversal trigger: if a future audit finds this clause is papering over an
# actually-missing review on a RECENT PR, tighten the cutoff or remove the
# clause and treat every ungrandfathered gap as FAIL.
ADR_0039_RECEIPT_TOOLING_LANDED_AT = "2026-08-28T09:50:53Z"

# The grandfather-only fallback: a structured PR comment naming the
# reviewing crew/vendor and a verdict token, which is what every real
# pre-cutoff cross-crew review in .omni/reviews.md actually looks like. This
# does NOT verify the commenter's identity differs from the author -- no
# mechanism here can, because every seat shares one GitHub PAT (ADR-0039's
# own named threat-model boundary); that gap is tracked separately as F-102
# (docs/audit/FINDINGS.md, owner: product) and is not this clause's job.
_REVIEW_COMMENT_VERDICT_RE = re.compile(
    r"(?i)\b(cross[- ]crew|cross[- ]vendor|different[- ]crew)\b.{0,160}?"
    r"\b(APPROVE[D]?|CLEAN PASS|CLEAR(?:ED)?(?: TO INTEGRATE)?|MERGE_BLOCKER|FAIL)\b",
    re.DOTALL,
)


def _has_valid_receipt_block(comments) -> bool:
    """A genuine ADR-0039 receipt: a `nightshift-review` fenced JSON block
    that bin/nightshift_review_receipts.py's own structural parser accepts
    (right keys, valid JSON, verdict/domain in its closed sets). Deliberately
    stops at the structural check -- the marker/freshness/PR-identity checks
    in validate_receipt() need a task_id, a head_sha and control.db access
    this validator does not have and is not designed to reach (stdlib +
    git/gh only, ADR-0025); that narrower class of forgery is out of scope
    here, the same way reviewer-identity is out of scope for this condition
    generally (see the D22 prose and F-102)."""
    for c in comments or []:
        body = (c.get("body") or "") if isinstance(c, dict) else ""
        try:
            block = review_receipts._extract_block(body)
        except review_receipts.ReceiptDenied:
            continue
        if block.get("domain") not in review_receipts.CANONICAL_DOMAINS:
            continue
        if block.get("verdict") not in review_receipts.VERDICTS:
            continue
        return True
    return False


# NOTE -- a third accepted evidence form (a "SHA-bound structured review
# comment": reviewed SHA + reviewing seat + verdict token) was implemented in
# the first round of this task and REMOVED before merge. It is recorded here
# because the reason it failed is a trap the next attempt must not re-enter.
#
# Its stated compensating control was that the reviewed SHA had to "resolve to
# a commit reachable from the PR's own head", which its docstring claimed gave
# "the same binding the fenced receipt's `head_sha` field provides". That is
# false, and `security` proved it with one constant 44-character string --
# `staff review: PASS a73ba881c04a4e5e2b716f89a0beb08a6e1164dc` -- which
# satisfies the whole form for 216 of 216 in-range PRs, because it names
# condition 22's own baseline commit that every PR in range descends from.
# Reachability proves ANCESTRY, not review of this PR's change.
#
# The failure was not the matcher's strictness, it was that the form required
# no semantic relationship between its three signals: an author's own
# "Rebased onto <sha>; CI PASS" satisfies it with NO INTENT TO ATTEST. A rule
# that manufactures false PASSes without needing a bad actor has no place in
# the one condition whose entire purpose is proving review happened.
#
# A replacement must satisfy BOTH remedies -- neither alone is sufficient:
#   (a) bind the SHA to the PR's OWN commits: reachable from the head but NOT
#       from `mergeCommit^1`. Measured: keeps 30 of 33 genuine reviews and
#       rejects the universal string. (Requiring the exact head is too tight --
#       it drops 18 of 33, because real reviews cite round-1 SHAs.)
#   (b) require an explicit structured assertion binding reviewer seat,
#       reviewed SHA and verdict together as a review CLAIM -- (a) alone still
#       admits the author's own rebase note, whose head is not an ancestor of
#       `mergeCommit^1`.
# THAT SUCCESSOR NOW EXISTS: form (4), below, which implements BOTH remedies
# and carries `architect`'s four adversarial cases and `security`'s four
# defeating strings as tests. It is deliberately NOT reachable from this
# function -- form (4) needs git and the PR's own head/merge SHAs, which this
# two-argument signature has no access to, and `check_d22` consults it only
# after everything here has declined. Read form (4)'s block comment before
# treating one of its acceptances as equal to anything accepted here: it is
# an after-the-fact attestation, it is the weakest thing this condition
# accepts, and it is counted and named in the emitted verdict line for
# exactly that reason.
def _has_recorded_review(data: dict, grandfathered: bool) -> bool:
    if data.get("reviews"):
        return True
    comments = data.get("comments") or []
    if _has_valid_receipt_block(comments):
        return True
    if grandfathered:
        for c in comments:
            body = (c.get("body") or "") if isinstance(c, dict) else ""
            if _REVIEW_COMMENT_VERDICT_RE.search(body):
                return True
    return False


# --- Evidence form (4): the RETROACTIVE structured review form -------------
#
# WHAT THIS PROVES, AND WHAT IT DOES NOT. Read this before treating an
# acceptance here as equivalent to anything else on this list.
#
# A form (4) acceptance is an ATTESTATION FILED AFTER THE FACT by whoever
# typed it. It is strictly WEAKER than an ADR-0039 receipt, and weaker in a
# way no amount of syntax can repair:
#
#   * a live receipt's `marker` is minted by the control plane against a
#     specific task and is verifiable there; a form's fields are all typed by
#     the filer, and this validator may not reach the control plane to check
#     any of them (ADR-0025);
#   * a live receipt is written while the review is happening; a form is
#     written afterwards, by someone reading a review they may not have
#     performed;
#   * neither form nor receipt can prove the FILER is not the AUTHOR -- every
#     seat shares one GitHub PAT (F-102, docs/audit/FINDINGS.md).
#
# So this form does not prove a review happened. It proves that a named seat
# has PUT ITS NAME to the claim that one did, in a shape that cannot be
# produced accidentally by ordinary PR prose. That is the whole of its claim,
# it is a real but small step above nothing, and `check_d22` therefore counts
# every PR that rests on it and SAYS SO IN THE EMITTED VERDICT LINE --
# `PASS (retro: N)`, not a bare `PASS`. A check whose emitted claim exceeds
# what it verified is the exact defect this whole condition exists to remove.
#
# WHY IT EXISTS ANYWAY. `security`'s replay of the 264-PR corpus classified
# ~48 code-touching PRs that carry a real, substantive cross-crew review
# (shortest 655 bytes, median 5,628) which the validator cannot bind to the
# PR. Those are a TOOLING gap, not a review gap, and they are distinct from
# the ~50 PRs with no review evidence anywhere, which are a remediation
# backlog this form must not be allowed to launder. Keeping the two
# populations distinguishable in the emitted output is why `retro:` is a
# separate count rather than folded into a silent PASS.
#
# THE TWO BINDINGS, REQUIRED TOGETHER. Form (3) was withdrawn because one
# constant string satisfied it for 216 of 216 in-range PRs. Neither remedy
# alone closes that:
#
#   (a) THE SHA IS ONE OF THE PR'S OWN COMMITS -- reachable from the PR head
#       and NOT reachable from `mergeCommit^1`. `security` measured this:
#       it rejects the universal baseline string and keeps 30 of 33 genuine
#       reviews (requiring the exact head drops 18 of 33, because real
#       reviews cite round-1 SHAs). Alone it still admits an author's own
#       "Rebased onto <sha>; CI PASS", whose SHA is also not an ancestor of
#       `mergeCommit^1`.
#   (b) THE CLAIM IS EXPLICIT AND PARSED AS A UNIT -- seat, SHA, verdict,
#       filing time and a fixed assertion sentence, all inside one fenced
#       block, so no combination of unrelated prose clauses can assemble a
#       form out of a comment that was not filing one. Alone it would be
#       satisfiable by a form naming any SHA at all.
#
# FAIL-CLOSED SYNTAX (`architect`'s reintroduction condition). A partially
# present form is not a weaker pass, it is refused outright: a missing field,
# a duplicated field, an unknown field, an empty value, a line that is not
# `key: value`, a body carrying more than one form block, an unknown seat, a
# lowercase or unknown verdict, an abbreviated SHA, an unparseable timestamp,
# or an assertion sentence that is not the canonical one -- every one of
# these yields NO evidence, never partial credit. There is exactly one shape
# that is accepted and everything else is refused.
#
# The fence tag is deliberately NOT `nightshift-review`: that parser takes
# the FIRST such fence in a body with no disambiguation (`security` FU-3, now
# #601), so a review discussing receipts hijacks its own receipt. A body
# containing two forms is refused here rather than silently resolved, which
# is the same defect answered the other way -- and a form that is merely
# QUOTED (nested in another fence, or indented as a code block) files nothing,
# which is `security`'s MB-2 and is enforced by a nesting-aware scanner rather
# than claimed by a docstring. See _top_level_fenced_blocks.
#
# THE FORM IS RETROACTIVE-ONLY, AND THAT BOUND IS ANCESTRY, NOT AN ORDERING
# AND NOT A CLOCK. The PR's merge commit must be an ancestor of
# FORM_4_ELIGIBLE_THROUGH -- the tip `main` stood at when the 224-PR corpus
# was audited -- so the eligible set IS the audited set, and merging more work
# moves `main` while never moving the pin. An ordering alone
# (`filed-at > mergedAt`) proves POSTERIORITY, not retroactivity; it shipped in
# round 1 and four domains found it. The ordering is still required, but it is
# measured on the COMMENT's `createdAt` from GitHub, not on the `filed-at` the
# filer typed. See _has_retro_review_form.
#
# The canonical shape, exactly:
#
#     ```nightshift-retro-review
#     reviewer-seat: architect
#     reviewed-sha: <40 lowercase hex, one of THIS PR's own commits>
#     verdict: PASS
#     filed-at: 2026-09-02T18:00:00Z     <- DECLARED; the control is the
#                                            comment's own GitHub `createdAt`
#     attests: I reviewed this pull request's changes at reviewed-sha and
#       the verdict above is that review's own finding.
#     ```
#
# NOT POPULATED BY THIS CHANGE. Filing forms against the ~48 historical PRs
# is a separate decision with its own disclosure requirements. This change
# builds the mechanism only; at this commit no form exists in any comment and
# `retro:` is 0 for the whole corpus.

_REVIEW_FORM_FENCE_TAG = "nightshift-retro-review"
# MB-2 (`security` S-1, round 1). The first implementation found form blocks
# with one regex, `^[ \t]*```<tag>...```$` under MULTILINE, and its own comment
# claimed "a comment that quotes a form must not be able to file one". That
# claim was FALSE, and it was false in the same class as form (3)'s: a control
# asserted in a docstring and not implemented. Measured by `security` at
# 224/224 -- an illustrative form quoted inside an outer four-backtick fence,
# and the same form as a four-space-indented code block, both FILED. `^[ \t]*`
# means indentation is not the disambiguator it looks like, and a regex that
# cannot see nesting cannot tell an example from a filing. Only `>`
# blockquoting was safe, and only by accident.
#
# So fence identification is a line scanner that understands CommonMark
# nesting instead: a form is only a filing when its fence opens at the TOP
# LEVEL of the comment -- not inside another fence of any length or character,
# not indented four or more columns (which is an indented code block, not a
# fence), and not inside a blockquote. An unterminated fence yields nothing.
# `security` wrote its entire round-1 review with no example form in it,
# because posting one would have filed a receipt; after this, quoting one is
# the ordinary safe thing it should always have been.
_FENCE_LINE = re.compile(r"^(?P<indent>[ \t]*)(?P<ticks>`{3,}|~{3,})(?P<info>.*)$")

# R2 / PD-1 (`security`, round 4). Every ancestry question form (4) rests on
# is asked with replace refs DISABLED.
#
# `git replace --graft 00eb294 <post-pin commit>` rewrites what "ancestor"
# means for every later reader, with no PAT, no network, no change to
# FORM_4_ELIGIBLE_THROUGH and NOTHING IN THE DIFF. `security` measured it
# flipping _pr_is_form_4_eligible False -> True end to end for a PR merged
# next December. It is not a privilege escalation -- it needs checkout write,
# and with that you could edit this validator outright -- but it defeats
# exactly what ADR-0075 exists to protect: the pin moves only with a re-run of
# the audit and a changelog row, and a replace ref moves it with neither, and
# invisibly. `--no-replace-objects` restores rc 1 with the ref still present.
#
# Applied to the SHA binding as well as the eligibility probe: both are
# ancestry controls and both are graftable, so exempting one would leave the
# same hole one bound over.
_GIT_ANCESTRY_ARGV = ["--no-replace-objects", "merge-base", "--is-ancestor"]


def _top_level_fenced_blocks(body: str, tag: str):
    """Bodies of every fenced block that opens at the TOP LEVEL of `body` and
    whose info string is exactly `tag`.

    TOP LEVEL MEANS COLUMN 0. Not nested inside another open fence, not
    indented by even one column, and not inside a blockquote. Fail-closed at
    both ends -- an unterminated fence contributes nothing, because a block
    nobody closed is not a filed form.

    Round 4 (`security`, R1) re-opened MB-2 on the first version of this rule,
    which refused an opener at `indent >= 4`. A fence inside a LIST ITEM is
    indented RELATIVE TO ITS MARKER, so `- file this instead:` followed by an
    indented form renders as a nested code block on GitHub while this scanner
    read it as a top-level filing: 224/224, and more likely in practice than
    the four-backtick case that made MB-2 a blocker in the first place. Any
    "how much indent is still top level" rule has this shape; column 0 has no
    arithmetic in it to get wrong.

    EVERY PROPERTY BELOW IS NAMED WITH THE TEST THAT PINS IT. Round 5 found
    five mutants surviving across two reviews and they were one defect wearing
    five hats: this docstring stated rules nothing asserted. `security` put it
    exactly -- "the docstring states four closer properties; only length is
    asserted". A docstring listing four properties with one asserted is the
    vacuous-evidence shape this whole condition exists to remove, so the rule
    here is now: ASSERT IT OR STOP STATING IT.

    Round 6 applied that rule TO THIS MAP. Two of its eight entries cited
    fixtures that predate the property and cannot discriminate it -- the
    "exactly the tag" and "at least as long" rows below. Both citations were
    re-measured, not re-reasoned. The lesson is the round's own, landing on
    itself: an entry pointing at a PRE-EXISTING fixture rather than one
    written for the property is where a map goes wrong.

    Opener, to open a block at all:
      * at column 0, not one column in
        -> test_an_indented_opener_is_refused_even_when_its_closer_is_at_column_0
           (subTested from ONE column, which is where `staff`'s surviving
           `indent > 1` mutant lived)
      * not already inside an open fence, and not inside a blockquote (a `>`
        prefix means the line is not a fence line at all)
        -> test_a_quoted_form_files_nothing
      * info string EXACTLY the tag, to be collected
        -> test_the_syntax_fails_closed_on_every_deviation, rows
           "tag with a SUFFIX -- security's `-EXAMPLE` convention",
           "tag with a suffix after a separator" and "tag with a PREFIX"

        Round 6 corrected this entry. It used to cite the "wrong fence tag"
        row, whose fixture (`nightshift-review-form`) differs in a PREFIX
        position and therefore pins "a DIFFERENT tag is refused", not
        "EXACTLY". `info == tag` -> `.startswith(` survived all 76 tests, and
        a form tagged `nightshift-retro-review-EXAMPLE` went from 0/224 to
        224/224 -- `-EXAMPLE` being the very convention `security` used in
        its own comments on this PR to name the form without filing one.

    Closer, to close it -- all four, and each pinned on its own:
      * same fence CHARACTER as the opener
        -> test_a_closer_using_a_different_fence_character_does_not_close
      * at least as LONG as the opener
        -> test_quoting_a_form_next_to_a_real_one_still_files_the_real_one,
           and the >= direction by test_a_longer_closer_still_closes_the_block

        Round 6 corrected this entry too. It used to cite
        test_a_quoted_form_files_nothing, which stays GREEN when
        `len(ticks) >= fence[1]` is removed -- that fixture's refusal is
        carried by the info-string and collecting-is-None checks, not by
        length. Re-measured here rather than taken on report: deleting the
        length clause reddens exactly one test, the one now cited.
      * no INFO STRING
        -> test_a_closer_carrying_an_info_string_does_not_close
      * at column 0
        -> test_a_form_whose_closer_is_not_at_column_0_never_closes

    And the fail-closed end: an unterminated fence contributes nothing
      -> test_an_unterminated_form_fence_yields_nothing
    """
    blocks = []
    fence = None      # (fence char, length) of the currently open fence
    collecting = None  # accumulating lines of a tagged block, or None
    for raw in (body or "").splitlines():
        line = raw.rstrip("\r")
        m = _FENCE_LINE.match(line)
        if m is None:
            if collecting is not None:
                collecting.append(line)
            continue
        indent = len(m.group("indent").expandtabs(4))
        ticks, info = m.group("ticks"), m.group("info").strip()
        if fence is None:
            # Anything indented at all is CONTENT -- of a list item, a
            # blockquote, or an indented code block -- not a top-level fence
            # opener. `^[ \t]*` accepted all three; `indent >= 4` still
            # accepted the list item.
            if indent:
                continue
            fence = (ticks[0], len(ticks))
            collecting = [] if info == tag else None
            continue
        # Inside a fence: only a bare run of the SAME character, at least as
        # long as the opener, at column 0, closes it. Everything else is
        # literal content -- which is precisely why a ```<tag> line inside an
        # outer ```` fence is an example, not a filing. All four conditions
        # are pinned separately; see the docstring's map.
        if ticks[0] == fence[0] and len(ticks) >= fence[1] and not info and not indent:
            if collecting is not None:
                blocks.append("\n".join(collecting))
            fence, collecting = None, None
        elif collecting is not None:
            collecting.append(line)
    return blocks
_REVIEW_FORM_LINE = re.compile(r"^([a-z][a-z-]*):[ \t]*(.*)$")
_REVIEW_FORM_FIELDS = frozenset({
    "reviewer-seat", "reviewed-sha", "verdict", "filed-at", "attests"})
# Uppercase-only, a closed set of two. `security` measured that uppercase-only
# matching is what closes the English-word risk (a lowercase "pass" occurs in
# ordinary prose constantly); and only PASS satisfies condition 22 -- a filed
# FAIL is a recorded review that did NOT clear, which is not evidence the
# change was reviewed and accepted.
_REVIEW_FORM_VERDICTS = ("PASS", "FAIL")
_REVIEW_FORM_CLEARING_VERDICT = "PASS"
_REVIEW_FORM_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# The seats that may file a form. Mirrors bin/nightshift-ctl's own
# EXEMPT_AGENTS plus ONCALL_AGENTS (bin/nightshift-ctl:224-228) rather than
# inventing a parallel roster; asserted as a subset of that literal by
# test_form_seat_roster_is_a_subset_of_nightshift_ctls_own, so the two cannot
# drift apart in silence. Copied rather than imported because `nightshift-ctl`
# is a hyphenated script owned by another crew and importing it would drag its
# whole control-plane import graph into a validator ADR-0025 confines to
# stdlib + git/gh.
_REVIEW_FORM_SEATS = frozenset({
    "product", "architect", "crew_alpha", "crew_beta", "pm", "beta_pm",
    "ux", "staff", "platform", "security", "release", "oncall",
})
# One fixed sentence, compared after case-folding and whitespace collapse and
# with trailing punctuation stripped -- so it can be line-wrapped in a comment
# without becoming unfileable, but cannot be paraphrased into something that
# asserts less. This is the "explicit review assertion": a statement that a
# review was performed, not a token that co-occurs with one.
_REVIEW_FORM_ASSERTION = (
    "i reviewed this pull request's changes at reviewed-sha and the verdict "
    "above is that review's own finding")


def _normalise_assertion(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().rstrip(".").casefold()


def _parse_iso8601(text: str):
    """A timezone-aware datetime, or None. Fail-closed: anything this cannot
    parse into an instant is not a timestamp for this form's purposes."""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # an instant with no zone is not an instant
    return parsed


def _parse_review_form(body: str):
    """The single well-formed form in one comment body, as a dict, or None.

    FAIL-CLOSED THROUGHOUT: every deviation returns None. See the block
    comment above for the enumerated list and why partial credit is refused.
    """
    blocks = _top_level_fenced_blocks(body, _REVIEW_FORM_FENCE_TAG)
    if len(blocks) != 1:
        # 0 -> nothing filed. >1 -> ambiguous about which one attests, and a
        # comment that quotes a form must not be able to file one.
        return None
    fields = {}
    for raw_line in blocks[0].splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _REVIEW_FORM_LINE.match(line)
        if m and m.group(1) in _REVIEW_FORM_FIELDS:
            key, value = m.group(1), m.group(2).strip()
            if key in fields or not value:
                return None  # duplicated, or present-but-empty
            fields[key] = value
            continue
        # A continuation line of the PREVIOUS field, so `attests:` can wrap.
        # Anything before the first field, or any line whose own `key:` is not
        # a form field, is junk and refuses the block.
        if not fields or (m and m.group(1) not in _REVIEW_FORM_FIELDS):
            return None
        last = list(fields)[-1]
        fields[last] = f"{fields[last]} {line}"
    if set(fields) != set(_REVIEW_FORM_FIELDS):
        return None  # missing field
    if fields["reviewer-seat"] not in _REVIEW_FORM_SEATS:
        return None
    if fields["verdict"] not in _REVIEW_FORM_VERDICTS:
        return None
    if not _REVIEW_FORM_SHA_RE.match(fields["reviewed-sha"]):
        return None
    if _normalise_assertion(fields["attests"]) != _REVIEW_FORM_ASSERTION:
        return None
    filed_at = _parse_iso8601(fields["filed-at"])
    if filed_at is None:
        return None
    fields["_filed_at"] = filed_at
    return fields


def _sha_is_one_of_this_prs_own_commits(ctx: Ctx, data: dict, sha: str) -> bool:
    """`security`'s measured binding: reachable from the PR head AND NOT
    reachable from `mergeCommit^1`.

    Both halves are asked of git and both must answer. A git call that does
    not answer at all (a bad object, a missing merge commit, any status other
    than the exact one required) yields False rather than raising: refusing to
    COUNT this evidence can only make condition 22 stricter, never falsely
    PASS it, so an unavailable git degrades to "no evidence" instead of
    BLOCKING the whole condition on an optional, weakest-form check.

    BOTH HALVES need git's EXACT status and neither may fall through on a
    non-answer:
      * reachable from the head, rc 0 exactly
        -> test_the_head_half_also_needs_gits_exact_answer
      * NOT reachable from `mergeCommit^1`, rc 1 exactly
        -> test_the_exclusion_half_needs_gits_exact_no_not_a_non_answer
    Neither was pinned before round 5, for one structural reason: the
    refuses-everything fake fails the ELIGIBILITY probe first, so no test
    reached either line. Each is now driven by a fake that grants everything
    except the one question it is about.

    That degradation is load-bearing in the other direction too, and it is why
    tests/test_dod_validate_d22.py drives this path under a git fake that
    GRANTS bindings rather than one that refuses them -- under a refusing fake
    every form is rejected for want of git, and a guard asserting "no form was
    accepted" would go green while a live evidence path sat in the validator.
    """
    head = data.get("headRefOid")
    merge_commit = data.get("mergeCommit")
    merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if not head or not merge_oid:
        return False
    inside = run_git(_GIT_ANCESTRY_ARGV + [sha, head], cwd=ctx.repo_root)
    if inside.returncode != 0:
        return False  # not reachable from the head, or git could not say
    outside = run_git(_GIT_ANCESTRY_ARGV + [sha, f"{merge_oid}^1"],
                      cwd=ctx.repo_root)
    # EXACTLY 1 -- git's "no, not an ancestor". Any other status (128 for a
    # bad object, anything else) is not an answer and must not be read as one.
    #
    # R5-1. `!= 0` here survived all 69 tests, and it is not a style point:
    # under a git that grants the head binding and answers 128 to this
    # question -- an UNFETCHED MERGE PARENT, a SQUASH PARENT, or a SHALLOW
    # CLONE, all ordinary environments rather than an attacker -- the mutant
    # ACCEPTS form (3)'s universal baseline constant. The 216/216 defeat,
    # resurrected by a checkout depth. Identical in cause to Mutant J, in
    # Mutant J's sibling half: the existing fail-closed test used the
    # refuses-everything fake, so `inside` failed first and this line was
    # never reached. Pinned by
    # test_the_exclusion_half_needs_gits_exact_no_not_a_non_answer, which
    # grants `inside` and answers 128 only here.
    return outside.returncode == 1


# MB-1 (round 1, found independently by `architect`, `crew_alpha`, `staff` and
# `crew_beta`, by four different methods). The first implementation accepted a
# form on `filed-at > mergedAt` and put NO UPPER BOUND on `mergedAt` anywhere
# in the acceptance path. `crew_alpha` executed the proof -- two scenarios
# differing only in `mergedAt`, both `PASS | retro: 1` -- and `crew_beta` named
# the shape exactly right:
#
#     `filed-at > mergedAt` proves POSTERIORITY, not RETROACTIVITY --
#     the same shape as form (3)'s reachability proving ancestry, not review.
#
# So a PR merging next December, with ADR-0039 receipts available and simply
# unused, was rescued on identical terms to the 48 genuine historical cases,
# and the disclosure line stayed honest while the population it described grew
# without limit. That is the exact decay the honesty requirement exists to
# prevent, which is why it was a blocker and not a follow-up.
#
# THE BOUND IS ANCESTRY, NOT A CLOCK. A PR is eligible for form (4) if and
# only if its merge commit is an ancestor of FORM_4_ELIGIBLE_THROUGH. That
# pinned SHA is the tip `main` stood at when the 224-PR corpus was audited, so
# the eligible set IS the audited set -- "the population we actually looked
# at", rather than "everything before a date we hope means that".
#
# Round 2 chose this over BOTH date anchors that round 1 offered, and the
# reason is worth keeping because it is not obvious:
#
#   * A calendar cutoff of midnight UTC has a residual that GROWS. It measured
#     zero when it was written and would have been FIVE by the next morning --
#     #626, #627, #624, #594 and #596 were all due to merge that night, before
#     the constant's own instant. Five PRs with ADR-0039 receipts available
#     would have become retro-attestable. That is MB-1 itself re-entering
#     through the anchor.
#   * An anchor at a PAST instant has no residual but strands real work: #582
#     was merged directly, that same day, with no review, and it is in the
#     ~51-no-evidence population. Any anchor that excludes "today" would put
#     it permanently beyond remediation. Ancestry includes it.
#   * Ancestry reads NEITHER clock. Round 1 found that git's committer clock
#     and GitHub's merge clock disagree by one second on the very commit that
#     would define a date boundary (`git log -1 --format=%cI 00eb294` is
#     2026-09-02T18:08:26Z; `gh pr view 587 --json mergedAt` is ...:27Z). An
#     ancestry test never reads either, so the skew stops being something
#     anyone has to argue about.
#
# Verified before the ruling: #1, #564, #582 and #587 all resolve ELIGIBLE
# (ancestors of the pin) and nothing is outside it -- `git rev-list --count
# 00eb294..origin/main` is 0, and unlike a calendar anchor that count cannot
# grow, because merging more work moves `main` and never moves the pin.
#
# It is NOT the allowlist that was rejected: one pinned SHA, not 48 enumerated
# PR numbers, and no maintenance burden. It stays consistent with `staff`'s
# D22 scope ruling, which accepted retro-review of the ~51 no-evidence PRs.
#
# THIS COMMENT IS NOT THE CONTROL. The decision, both rejected options and the
# advancement trigger are recorded in
# docs/decisions/ADR-0075-form-4-retro-eligibility-pinned-by-ancestry.md
# (accepted), and test_the_pin_is_the_one_this_adr_accepted asserts this
# constant equals the SHA that ADR accepted. `architect` returned FAIL on the
# earlier version of this file, in which the pin and its reversal rule existed
# ONLY here: every test stayed green if someone advanced the constant while
# updating or deleting these lines, so the protection was exactly as strong as
# a comment. Advancing the pin now requires superseding an accepted ADR.
#
# REVERSAL TRIGGER (normative text in ADR-0075): the pin moves only when the
# audited corpus is re-run at a later tip, and never to make a newer PR
# attestable.
FORM_4_ELIGIBLE_THROUGH = "00eb294508ccf10658e333840854993242dd7243"


def _pr_is_form_4_eligible(ctx: Ctx, data: dict) -> bool:
    """True iff this PR's merge commit is an ancestor of the pinned tip.

    Fail-closed on every uncertainty, for the same reason `createdAt` is:
    NO CLOCK, NO EVIDENCE became NO ANCESTRY, NO EVIDENCE. An absent merge
    commit, a SHA git does not know, git being unavailable at all -- each is
    a question that did not get answered, and an unanswered question is not a
    satisfied bound. Only git's own rc=0 counts as yes.
    """
    merge_commit = data.get("mergeCommit")
    merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if not merge_oid:
        return False
    probe = run_git(_GIT_ANCESTRY_ARGV + [merge_oid, FORM_4_ELIGIBLE_THROUGH],
                    cwd=ctx.repo_root)
    return probe.returncode == 0


def _has_retro_review_form(ctx: Ctx, data: dict) -> bool:
    """True iff this PR carries an accepted form (4) attestation.

    Only ever consulted AFTER the stronger forms have declined, so a PR
    counted here is one whose review evidence rests on a retroactive
    attestation and nothing else -- which is exactly the population the
    emitted `retro: N` reports.

    THREE separate bounds, and all three are required:

    1. ELIGIBILITY, BY ANCESTRY -- the PR's merge commit must be an ancestor
       of FORM_4_ELIGIBLE_THROUGH, the tip the 224-PR corpus was audited at.
       This is what makes the form retroactive rather than merely posterior,
       and it is what stops the form becoming a permanent second route around
       ADR-0039: merging more work moves `main` and never moves the pin, so
       the eligible set cannot grow. See the block comment above, including
       the reversal trigger.

    2. POSTERIORITY, MEASURED ON GITHUB'S CLOCK -- the COMMENT's own
       `createdAt`, not the `filed-at` the filer typed. `security`'s finding
       was that `filed-at` is "simply a field the filer types, verified
       against nothing", so a control resting on it rests on nothing.
       `gh pr view --json comments` returns `createdAt` for every comment (all
       224 in `security`'s fetch), and a comment with no `createdAt` files
       nothing -- no clock, no evidence.

       `filed-at` is a REQUIRED, DECLARED field and NOTHING RESTS ON ITS
       VALUE. It is parsed only far enough to reject a form that does not
       carry a real timestamp at all (fail-closed syntax, like every other
       field), and its value is then never compared to anything. It exists so
       a human reading the artifact sees a date the filer put there.

       It used to be compared against `mergedAt` as a "consistency check",
       and that was wrong in a way two reviewers had to prove: it made a
       filer-supplied field partly load-bearing, and on a RETROACTIVE
       attestation the natural `filed-at` is when the review actually
       happened -- plausibly BEFORE the merge. A filer told the field is
       inert would write exactly that and be silently refused. A field
       documented as inert that is in fact a rejection path is a trap laid
       for honest users, and it bought nothing: `createdAt` already enforces
       posteriority against a clock nobody types, so the gate could only ever
       cause a false FAIL, never a false PASS.

    3. The SHA binding, in _sha_is_one_of_this_prs_own_commits.

    Note what (2) does NOT close, so nobody reads more into it than it does:
    `createdAt` is the comment's creation instant, and an edit can change a
    comment's body afterwards (`includesCreatedEdit` reports that an edit
    happened, not what it changed). A comment created after the merge and
    edited later still satisfies posteriority honestly; the direction that
    would matter -- backdating a filing to before the merge -- is refused,
    because an earlier `createdAt` can only ever make this stricter.
    """
    merged_at = _parse_iso8601(data.get("mergedAt"))
    if merged_at is None:
        return False
    if not _pr_is_form_4_eligible(ctx, data):
        return False
    for c in data.get("comments") or []:
        if not isinstance(c, dict):
            continue
        created_at = _parse_iso8601(c.get("createdAt"))
        if created_at is None or created_at <= merged_at:
            continue
        form = _parse_review_form(c.get("body") or "")
        if form is None:
            continue
        if form["verdict"] != _REVIEW_FORM_CLEARING_VERDICT:
            continue
        if _sha_is_one_of_this_prs_own_commits(ctx, data, form["reviewed-sha"]):
            return True
    return False


# --- pr-lint kind, derived from the PR's own diff (never assumed) ----------
#
# `bin/nightshift-ctl pr-lint` defaults `--kind` to `task`
# (bin/nightshift-ctl:6648), and `lint_body()` applies the `Refs #<n>` rule
# only when kind == "task" (bin/nightshift-ctl:4270-4282). Invoking pr-lint
# with no --kind therefore ASSERTS that every PR in this project's history is
# a task PR. It is not: `.omni/**`-only program bookkeeping PRs have no
# backlog issue by construction (docs/owners.md:27), and `staff` measured
# roughly 20 of the 32 pr-lint failures to be exactly those.
#
# So derive the kind from evidence the PR itself carries -- its own changed
# file list -- rather than declaring on its behalf a kind it never declared.
_FREE_KIND_PREFIX = ".omni/"


def _pr_diff_is_determinable(data: dict) -> bool:
    """True iff this PR reports a usable changed-path list. False means the
    kind below is a fail-closed FALLBACK, not a derivation -- a distinction
    the reason line reports separately, because a gh outage that emptied
    every `files` array would otherwise read as a body-convention problem."""
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return False
    for f in files:
        path = (f.get("path") if isinstance(f, dict) else f) or ""
        if not isinstance(path, str) or not path:
            return False
    return True


def _pr_lint_kind(data: dict) -> str:
    """`free` iff EVERY changed path in this PR's own diff is under
    `.omni/**`; otherwise `task`.

    FAIL CLOSED. If the diff cannot be determined -- no `files` array, an
    empty one, or an entry with no usable path -- return `task`, the strictly
    STRONGER kind (it applies the `Refs #<n>` and closing-keyword rules that
    `free` does not). An undeterminable diff can then only ever produce MORE
    findings, never fewer. Guessing `free` would silently exempt a PR from
    the rules on the strength of missing evidence."""
    if not _pr_diff_is_determinable(data):
        return "task"
    for f in data["files"]:
        path = f.get("path") if isinstance(f, dict) else f
        if not path.startswith(_FREE_KIND_PREFIX):
            return "task"
    return "free"


def _merged_pr_numbers_in_range(ctx: Ctx, base_sha: str) -> set:
    """PR numbers reachable via a first-parent merge commit in base_sha..HEAD,
    read structurally off git's own 'Merge pull request #N from ...' message
    -- this project's actual, confirmed merge convention (every PR merges via
    a real merge commit, never a squash). A pure git-log instrument: never
    calls gh, so it costs nothing extra when the manifest is already current."""
    log = run_git(["log", "--first-parent", "--merges", "--pretty=%s", f"{base_sha}..HEAD"], cwd=ctx.repo_root)
    if log.returncode != 0:
        raise InstrumentUnavailable(f"git log --merges failed: {log.stderr.strip()[:200]}")
    nums = set()
    for line in log.stdout.splitlines():
        m = re.match(r"Merge pull request #(\d+)", line.strip())
        if m:
            nums.add(int(m.group(1)))
    return nums


def _baseline_pr_numbers(ctx: Ctx, base_sha: str) -> set:
    """PR numbers merged AT OR BEFORE condition 22's own baseline commit --
    i.e. reachable from `base_sha` itself rather than from `base_sha..HEAD`.

    Condition 22's declared range is `a73ba88..main`. Two PRs sit outside it
    by construction: #1's merge commit `a428fb5` is an ANCESTOR of the
    baseline, and #2's merge commit IS the baseline `a73ba88`. They were
    nonetheless being checked, because the manifest's `changes` list names
    them and the union pulled them back in past the range that defines this
    condition.

    This is a RANGE correction, not clemency. `staff` verified neither PR is
    cited in TRACEABILITY.md or FINDINGS.md, so nothing else depends on them
    being in the checked set. It is derived structurally from git rather than
    hardcoded as {1, 2}, so a future baseline move stays correct, and it is
    deliberately NOT a timestamp-keyed grandfather clause: `staff` proved no
    PR in the checked set predates the `Refs #<n>` rule. Re-verified here
    rather than taken on trust: the rule landed in `bin/omni-ctl` at
    `4f931e6` (2026-08-21T20:16:30+05:30), 18h45m10s before PR #1's merge
    commit (2026-08-22T15:01:40+05:30) -- the later `38fe32c` only RENAMED
    that file to `bin/nightshift-ctl` and did not introduce the rule. So such
    a clause would be a no-op that only obscured things."""
    log = run_git(["log", "--first-parent", "--merges", "--pretty=%s", base_sha], cwd=ctx.repo_root)
    if log.returncode != 0:
        raise InstrumentUnavailable(f"git log --merges {base_sha} failed: {log.stderr.strip()[:200]}")
    nums = set()
    for line in log.stdout.splitlines():
        m = re.match(r"Merge pull request #(\d+)", line.strip())
        if m:
            nums.add(int(m.group(1)))
    return nums


# WA-015: a check that stops at the first failure must say how many failures
# exist in total. A reason line naming ONE item when the check STOPPED at one
# item systematically under-scopes the work -- three consecutive heartbeats,
# Program's included, scoped a 251-PR problem as a 1-PR problem on exactly
# that reason line and deferred it each time as small. These two helpers keep
# the reason line honest: every failing class reports its full count, with a
# bounded sample so the line stays readable.
_REASON_EXAMPLE_LIMIT = 5


def _lint_problems(stdout: str):
    """The `problems` list from pr-lint's JSON, or the raw output as a single
    problem if it is not the JSON shape this expects. Never raises -- a
    surprising pr-lint output must not turn a FAIL into a crash."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return [stdout.strip()[:80]] if stdout.strip() else ["unparseable pr-lint output"]
    problems = payload.get("problems") if isinstance(payload, dict) else None
    if not isinstance(problems, list) or not problems:
        return ["unreported pr-lint problem"]
    return [p if isinstance(p, str) else str(p) for p in problems]


def _lint_problem_key(problem: str) -> str:
    """A stable bucket label for one pr-lint problem: the clause before its
    explanatory em-dash, with any trailing enumeration of specific issue
    numbers stripped, so `closing keyword(s) on a task PR: #190` and the same
    problem on another PR count as one kind rather than two."""
    head = re.split(r"\s+—\s+", problem, maxsplit=1)[0]
    head = re.split(r":\s", head, maxsplit=1)[0]
    return head.strip()[:60] or "unlabelled pr-lint problem"


def _fmt_examples(numbers) -> str:
    shown = sorted(numbers)[:_REASON_EXAMPLE_LIMIT]
    text = ", ".join(f"#{n}" for n in shown)
    remaining = len(numbers) - len(shown)
    if remaining > 0:
        text += f", +{remaining} more"
    return text


def _queued_gate_evidence(ctx: Ctx, manifest: dict):
    """Every gate name a PASS may cite as queued right now. `conditions.22.
    queued_gate` may be one `{gate, blocked_md_excerpt}` object or a list of
    them -- a checked PR set can legitimately be queued behind more than one
    currently-unavailable gate at once (crew_beta FOLLOW_UP on PR #526 round
    1: a single scalar under-reported which gates were actually pending).
    Each entry is independently verified, never taken on the manifest's
    say-so alone: its named excerpt must be present, verbatim, right now, in
    .omni/blocked.md -- the same discipline condition 21's
    frozen_path_control pointer uses. Returns the set of gate names whose
    excerpt actually verifies (may be empty)."""
    q = ((manifest.get("conditions") or {}).get("22") or {}).get("queued_gate")
    if not q:
        return set()
    entries = q if isinstance(q, list) else [q]
    blocked = ctx.repo_root / ".omni" / "blocked.md"
    if not blocked.exists():
        return set()
    text = blocked.read_text(encoding="utf-8", errors="replace")
    verified = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        gate = entry.get("gate")
        excerpt = entry.get("blocked_md_excerpt")
        if gate and excerpt and excerpt in text:
            verified.add(gate)
    return verified


def _retro_clause(retro_attested) -> str:
    """The `retro: N` clause. One wording, used on the PASS and the FAIL side
    alike, so the weakening is visible in whichever line gets printed."""
    return (f"retro: {len(retro_attested)} of these rest on a RETROACTIVE"
            f" form (4) attestation, which is weaker than a live ADR-0039"
            f" receipt ({_fmt_examples(retro_attested)})")


def check_d22(ctx: Ctx) -> Verdict:
    manifest = _need_manifest(ctx)
    changes = manifest.get("changes")
    if not changes:
        return verdict_fail("manifest declares no 'changes' (the PR list this condition checks)")
    change_prs = {c["pr"] if isinstance(c, dict) else c for c in changes}
    # Condition 22's own commit-range clause: a PR merged since the DoD's B1
    # baseline that the manifest's 'changes' list has not caught up with is
    # still checked here, not silently skipped because nobody added it.
    extra_prs = _merged_pr_numbers_in_range(ctx, D22_BASE_SHA) - change_prs
    # Condition 22's own range is `a73ba88..main`: a PR already merged AT OR
    # BEFORE the baseline is not a change this condition audits, even if the
    # manifest's `changes` list still names it. See _baseline_pr_numbers.
    baseline_prs = _baseline_pr_numbers(ctx, D22_BASE_SHA)
    checked = sorted((change_prs | extra_prs) - baseline_prs)
    if not checked:
        return verdict_fail(
            f"no PR falls inside condition 22's range ({D22_BASE_SHA[:7]}..HEAD) -- "
            "nothing was audited, which is never a PASS")

    ctl = ctx.repo_root / "bin" / "nightshift-ctl"
    queued_gates = []  # every distinct gate any checked PR is legitimately queued behind
    # WA-015: accumulate across the WHOLE corpus. Never return on the first
    # failing PR -- the totals below are the point of this condition.
    lint_failed = []
    review_failed = []
    review_failed_grandfathered = []
    # PRs whose review evidence is a form (4) retroactive attestation and
    # nothing stronger. Counted separately and NAMED IN THE VERDICT LINE --
    # never folded into a silent PASS. See form (4)'s block comment.
    retro_attested = []
    rollup_failed = []
    undeterminable_diff = []
    lint_problem_counts = {}

    for n in checked:
        pr = run_gh(["pr", "view", str(n), "--json",
                     "state,mergedAt,body,reviews,comments,statusCheckRollup,"
                     "headRefName,files,headRefOid,mergeCommit"])
        if pr.returncode != 0:
            raise InstrumentUnavailable(f"gh pr view {n} failed: {pr.stderr.strip()[:200]}")
        try:
            data = json.loads(pr.stdout)
        except json.JSONDecodeError:
            raise InstrumentUnavailable(f"gh pr view {n} returned non-JSON output")

        if ctl.exists():
            kind = _pr_lint_kind(data)
            if not _pr_diff_is_determinable(data):
                undeterminable_diff.append(n)
            fd, body_path = tempfile.mkstemp(suffix=".md")
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(data.get("body") or "")
                lint = subprocess.run(
                    [str(ctl), "pr-lint", "--kind", kind, "--body-file", body_path],
                    capture_output=True, text=True, timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                raise InstrumentUnavailable(f"nightshift-ctl pr-lint unavailable: {e}") from e
            finally:
                os.unlink(body_path)
            if lint.returncode != 0:
                lint_failed.append(n)
                # Bucket by the problem's own leading clause, so the reason
                # line says WHICH convention is being missed and how often --
                # "30 missing Refs, 1 closing keyword" is a work estimate;
                # one sampled PR body is not.
                for problem in _lint_problems(lint.stdout):
                    key = _lint_problem_key(problem)
                    lint_problem_counts[key] = lint_problem_counts.get(key, 0) + 1

        merged_at = data.get("mergedAt")
        grandfathered = bool(merged_at) and merged_at <= ADR_0039_RECEIPT_TOOLING_LANDED_AT
        if not _has_recorded_review(data, grandfathered):
            # Weakest form last, and only once everything stronger has
            # declined, so `retro_attested` is exactly the population resting
            # on an after-the-fact attestation.
            if _has_retro_review_form(ctx, data):
                retro_attested.append(n)
            else:
                review_failed.append(n)
                if grandfathered:
                    review_failed_grandfathered.append(n)

        if not data.get("statusCheckRollup"):
            if data.get("state") == "MERGED":
                rollup_failed.append(n)
            else:
                gates = _queued_gate_evidence(ctx, manifest)
                if not gates:
                    rollup_failed.append(n)
                for gate in gates:
                    if gate not in queued_gates:
                        queued_gates.append(gate)

    if lint_failed or review_failed or rollup_failed:
        distinct = sorted(set(lint_failed) | set(review_failed) | set(rollup_failed))
        # The headline counts PRs; the bracketed breakdown counts FINDINGS,
        # and one PR can carry several (#558 fails both conventions). Both
        # numbers were right and the line did not say which was which, which
        # cost a reconciliation cycle to discover -- so each states its unit.
        lint_part = f"pr-lint: {len(lint_failed)} PRs failing"
        if lint_failed:
            breakdown = ", ".join(
                f"{count}x {key}" for key, count in
                sorted(lint_problem_counts.items(), key=lambda kv: (-kv[1], kv[0])))
            lint_part += f" [findings: {breakdown}] ({_fmt_examples(lint_failed)})"
        # The grandfathered/post-cutoff split is the scoping number: a PR
        # merged AFTER the receipt tooling landed had the mechanism and did
        # not use it, which is a different problem from one that merged
        # before the mechanism existed. Reporting a single total hides that.
        post_cutoff = len(review_failed) - len(review_failed_grandfathered)
        review_part = (
            f"recorded review from a different crew/vendor: {len(review_failed)} failing")
        if review_failed:
            review_part += (
                f" ({len(review_failed_grandfathered)} merged at or before"
                f" {ADR_0039_RECEIPT_TOOLING_LANDED_AT} and fail even the grandfathered"
                f" prose bar; {post_cutoff} merged after it with no genuine ADR-0039"
                f" receipt) ({_fmt_examples(review_failed)})")
        parts = [
            f"{len(distinct)} of {len(checked)} checked PRs fail condition 22",
            lint_part,
            review_part,
            f"gate results: {len(rollup_failed)} failing"
            + (f" ({_fmt_examples(rollup_failed)})" if rollup_failed else ""),
        ]
        if undeterminable_diff:
            parts.append(
                f"pr-lint kind fell back to `task` (diff undeterminable, never assumed):"
                f" {len(undeterminable_diff)} ({_fmt_examples(undeterminable_diff)})")
        if retro_attested:
            parts.append(_retro_clause(retro_attested))
        return verdict_fail("; ".join(parts))

    reasons = []
    if queued_gates:
        reasons.append(f"queued: {', '.join(sorted(queued_gates))}")
    if retro_attested:
        # The honesty requirement, in the EMITTED OUTPUT and not only in
        # prose: a PASS carrying retroactive attestations is not the same
        # PASS as one carrying live receipts, and the line has to say so
        # every time it is printed, where no reader can miss it.
        reasons.append(_retro_clause(retro_attested))
    return verdict_pass("; ".join(reasons))


def break_d22(fixture_root: Path, manifest):
    m = copy.deepcopy(manifest) if manifest else {}
    m["changes"] = []
    return m


# --------------------------------------------------------------------------
# Condition 23 -- handover
# --------------------------------------------------------------------------

_HANDOVER_REQUIRED = (
    "what changed and why",
    "confirmed still broken",
    "accepted risk",
    "operator action",
    "rollback",
)


def check_d23(ctx: Ctx) -> Verdict:
    path = ctx.repo_root / "docs" / "HANDOVER.md"
    if not path.exists():
        return verdict_fail("docs/HANDOVER.md not found")
    text = path.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()
    missing = [s for s in _HANDOVER_REQUIRED if s not in lower]
    if missing:
        return verdict_fail(f"docs/HANDOVER.md is missing required content: {missing}")
    verify_match = re.search(r"^##\s*Verify\s*$(.*?)(?:^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not verify_match:
        return verdict_fail("docs/HANDOVER.md has no '## Verify' section")
    commands = re.findall(r"```(?:bash|sh)?\n(.*?)\n```", verify_match.group(1), re.DOTALL)
    if not commands:
        return verdict_fail("docs/HANDOVER.md '## Verify' section has no fenced command block")
    with tempfile.TemporaryDirectory(prefix="dod-d23-") as tmp:
        clone = run_git(["clone", "--local", "--quiet", str(ctx.repo_root), tmp], cwd=ctx.repo_root)
        if clone.returncode != 0:
            raise InstrumentUnavailable(f"git clone --local for the D23 verify step failed: {clone.stderr.strip()[:200]}")
        script = "\n".join(commands)
        try:
            proc = subprocess.run(["bash", "-c", script], cwd=tmp, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise InstrumentUnavailable(f"docs/HANDOVER.md '## Verify' commands did not complete: {e}") from e
        if proc.returncode != 0:
            return verdict_fail(f"docs/HANDOVER.md '## Verify' commands failed (exit {proc.returncode}): {proc.stderr.strip()[:300]}")
    return verdict_pass()


# --------------------------------------------------------------------------
# Condition 24 -- traceability
# --------------------------------------------------------------------------

_TRACEABILITY_OBLIGATION_COUNT = 18


_TRACE_RT_RE = re.compile(r"RT-\d+")
_TRACE_PR_RE = re.compile(r"#(\d+)")
_TRACE_F_RE = re.compile(r"F-\d+")
_TRACE_Q_RE = re.compile(r"(docs/audit/[\w.]+\.md)#Q(\d+)")

_TRACE_AUDIT_DOCS = (
    "docs/audit/CONCURRENCY.md", "docs/audit/SURVIVAL.md", "docs/audit/PIPELINE.md",
    "docs/audit/BOUNDARIES.md", "docs/audit/INSTALL.md",
)


def _all_known_finding_ids(ctx: Ctx):
    ids = set()
    findings_records, err = _parse_json_blocks(ctx.repo_root / "docs" / "audit" / "FINDINGS.md")
    if not err:
        for r in findings_records:
            if isinstance(r, dict) and r.get("id"):
                ids.add(str(r["id"]))
    for doc in _TRACE_AUDIT_DOCS:
        doc_ids = _audit_doc_finding_ids(ctx.repo_root, doc)
        if doc_ids:
            ids |= doc_ids
    return ids


def check_d24(ctx: Ctx) -> Verdict:
    path = ctx.repo_root / "docs" / "prd" / "TRACEABILITY.md"
    records, err = _parse_json_blocks(path)
    if err:
        return verdict_fail(err)
    if len(records) != _TRACEABILITY_OBLIGATION_COUNT:
        return verdict_fail(f"TRACEABILITY.md has {len(records)} obligation rows, expected exactly {_TRACEABILITY_OBLIGATION_COUNT}")
    manifest = _need_manifest(ctx)
    rt_ids = set((manifest.get("rt_ids") or {}).keys())
    change_prs = {c["pr"] if isinstance(c, dict) else c for c in (manifest.get("changes") or [])}
    finding_ids = _all_known_finding_ids(ctx)
    seen = set()
    for r in records:
        if not isinstance(r, dict) or not r.get("obligation"):
            return verdict_fail(f"a traceability row is missing 'obligation': {r!r}")
        ob = r["obligation"]
        if ob in seen:
            return verdict_fail(f"duplicate obligation row: {ob!r}")
        seen.add(ob)
        for field in ("condition", "questions", "findings", "rt_ids", "prs"):
            if not r.get(field):
                return verdict_fail(f"{ob}: empty evidence column {field!r}")
        conds = r["condition"] if isinstance(r["condition"], list) else [r["condition"]]
        for c in conds:
            if not isinstance(c, int) or not (1 <= c <= 24):
                return verdict_fail(f"{ob}: condition {c!r} does not resolve to a real DoD condition (1..24)")
        for q in r["questions"]:
            m = _TRACE_Q_RE.search(str(q))
            if not m:
                return verdict_fail(f"{ob}: question citation {q!r} does not resolve to a docs/audit/*.md#Qn id")
            docpath, qn = m.group(1), int(m.group(2))
            qrecords, qerr = _parse_json_blocks(ctx.repo_root / docpath)
            if qerr:
                return verdict_fail(f"{ob}: question citation {q!r} -- {docpath} does not resolve: {qerr}")
            qids = {str(rr.get("id")) for rr in qrecords if isinstance(rr, dict)}
            if f"Q{qn}" not in qids:
                return verdict_fail(f"{ob}: question citation {q!r} -- Q{qn} is not a real question id in {docpath}")
        for f in r["findings"]:
            if str(f).strip().lower().startswith("none"):
                continue
            for fid in _TRACE_F_RE.findall(str(f)):
                if fid not in finding_ids:
                    return verdict_fail(f"{ob}: finding citation {fid!r} does not resolve to a real finding id")
        for rt in r["rt_ids"]:
            for rtid in _TRACE_RT_RE.findall(str(rt)):
                if rtid not in rt_ids:
                    return verdict_fail(f"{ob}: RT id citation {rtid!r} does not resolve to a real RT id in {MANIFEST_REL}")
        for pr in r["prs"]:
            for prn in _TRACE_PR_RE.findall(str(pr)):
                if int(prn) not in change_prs:
                    return verdict_fail(f"{ob}: PR citation {pr!r} does not resolve to a PR tracked in the manifest's 'changes' list")
    return verdict_pass()


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

@dataclass
class Check:
    id: str
    condition: int
    run: Callable[[Ctx], Verdict]
    break_evidence: Callable[[Path, Optional[dict]], Optional[dict]]


REGISTRY = [
    Check("D01", 1, generic_rt_condition(1, structural=[_bash_n_check("deploy/provision-ubuntu.sh")]), break_generic(1)),
    Check("D02", 2, generic_rt_condition(2), break_generic(2)),
    Check("D03", 3, generic_rt_condition(3, structural=[_six_units_named_check]), break_generic(3)),
    Check("D04", 4, generic_rt_condition(4), break_generic(4)),
    Check("D05", 5, generic_rt_condition(5, structural=[_grep_absent_check("docs/TRUST-BOUNDARY.md", "AppArmor profile on 24.04")]), break_generic(5)),
    Check("D06", 6, generic_rt_condition(6), break_generic(6)),
    Check("D07", 7, generic_rt_condition(7), break_generic(7)),
    Check("D08", 8, check_d08, break_d08),
    Check("D09", 9, check_d09, break_d09),
    Check("D10", 10, check_d10, break_d10),
    Check("D11", 11, check_d11, break_d11),
    Check("D12", 12, check_d12, break_d12),
    Check("D13", 13, audit_doc_check("docs/audit/CONCURRENCY.md", 5), break_doc("docs/audit/CONCURRENCY.md")),
    Check("D14", 14, audit_doc_check("docs/audit/SURVIVAL.md", 4), break_doc("docs/audit/SURVIVAL.md")),
    Check("D15", 15, audit_doc_check("docs/audit/PIPELINE.md", 4), break_doc("docs/audit/PIPELINE.md")),
    Check("D16", 16, audit_doc_check("docs/audit/BOUNDARIES.md", 4), break_doc("docs/audit/BOUNDARIES.md")),
    Check("D17", 17, check_d17, break_doc("docs/audit/INSTALL.md")),
    Check("D18", 18, check_d18, break_doc("docs/audit/FINDINGS.md")),
    Check("D19", 19, check_d19, break_d19),
    Check("D20", 20, check_d20, break_d20),
    Check("D21", 21, check_d21, break_d21),
    Check("D22", 22, check_d22, break_d22),
    Check("D23", 23, check_d23, break_doc("docs/HANDOVER.md")),
    Check("D24", 24, check_d24, break_doc("docs/prd/TRACEABILITY.md")),
]

assert [c.id for c in REGISTRY] == [f"D{n:02d}" for n in range(1, 25)], "registry ids must be D01..D24 in order"
assert all(c.condition == n for n, c in enumerate(REGISTRY, start=1)), "registry conditions must be 1..24 in order"


# --------------------------------------------------------------------------
# --json (Issue #423-A / ADR-0063): a per-condition blocking artifact, for
# every condition whose check is backed by exactly one named file (an audit
# doc, HANDOVER.md, TRACEABILITY.md, TRUST-BOUNDARY.md, or the DoD manifest
# itself for the two manifest-section checks). Conditions with no single
# derivable file -- a test-node run, a git-history scan, a multi-file
# structural check -- are deliberately absent here and report
# `blocking_artifact: null`, per AC-1's own "null when none derivable"
# clause; this is not an oversight to fill in later, it is the schema's
# stated failure mode.
# --------------------------------------------------------------------------
BLOCKING_ARTIFACT_BY_ID = {
    "D05": "docs/TRUST-BOUNDARY.md",
    "D13": "docs/audit/CONCURRENCY.md",
    "D14": "docs/audit/SURVIVAL.md",
    "D15": "docs/audit/PIPELINE.md",
    "D16": "docs/audit/BOUNDARIES.md",
    "D17": "docs/audit/INSTALL.md",
    "D18": "docs/audit/FINDINGS.md",
    "D19": str(MANIFEST_REL),
    "D20": str(MANIFEST_REL),
    "D23": "docs/HANDOVER.md",
    "D24": "docs/prd/TRACEABILITY.md",
}

DOD_JSON_SCHEMA_VERSION = 1


def _blocking_artifact_for(check_id: str, verdict: "Verdict") -> Optional[str]:
    """Only a FAIL ever names a blocking artifact -- PASS has nothing to
    block, and BLOCKED/MANUAL are instrument/human states, not file-shaped
    ones (ADR-0063, BLOCKED/MANUAL never fold into FAIL)."""
    if verdict.status != Status.FAIL:
        return None
    return BLOCKING_ARTIFACT_BY_ID.get(check_id)


def run_check_safely(check: Check, ctx: Ctx) -> Verdict:
    try:
        v = check.run(ctx)
        if not isinstance(v, Verdict):
            return Verdict(Status.FAIL, f"validator error: check returned {type(v).__name__}, not a Verdict")
        if v.status == Status.MANUAL and check.condition not in MANUAL_CONDITIONS:
            return Verdict(Status.FAIL, f"validator error: MANUAL is not permitted for condition {check.condition}")
        return v
    except InstrumentUnavailable as e:
        return Verdict(Status.BLOCKED, str(e))
    except ManifestMissing as e:
        return Verdict(Status.FAIL, str(e) or f"{MANIFEST_REL} not found")
    except Exception as e:  # noqa: BLE001 -- deliberate: AC6, one bad condition never stops the other 23
        return Verdict(Status.FAIL, f"validator error: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------
# --self-test
# --------------------------------------------------------------------------

def run_self_test() -> bool:
    manifest, err = load_manifest(REPO_ROOT)
    if manifest is None:
        print(
            f"SELF-TEST ABORTED: cannot load the real manifest ({err}); --self-test needs a real "
            f"{MANIFEST_REL} at HEAD to exercise the removal path against.",
            file=sys.stderr,
        )
        return False
    with tempfile.TemporaryDirectory(prefix="dod-selftest-") as base_parent:
        base = Path(base_parent) / "pristine"
        wt = run_git(["worktree", "add", "--detach", str(base), "HEAD"], cwd=REPO_ROOT)
        if wt.returncode != 0:
            print(f"SELF-TEST ABORTED: git worktree add failed: {wt.stderr.strip()}", file=sys.stderr)
            return False
        try:
            all_ok = True
            for check in REGISTRY:
                scratch = Path(base_parent) / f"scratch-{check.id}"
                shutil.copytree(base, scratch, symlinks=True)
                try:
                    mutated = check.break_evidence(scratch, copy.deepcopy(manifest))
                    ctx = Ctx(scratch, mutated, None)
                    verdict = run_check_safely(check, ctx)
                    ok = verdict.status != Status.PASS
                    all_ok = all_ok and ok
                    tag = "ok" if ok else "FAILED TO FLIP AWAY FROM PASS"
                    print(f"{check.id} self-test {tag}: post-removal verdict {verdict.status} {verdict.reason}")
                finally:
                    shutil.rmtree(scratch, ignore_errors=True)
            return all_ok
        finally:
            run_git(["worktree", "remove", "--force", str(base)], cwd=REPO_ROOT)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The DoD close-out gate (docs/prd/DEFINITION-OF-DONE.md).")
    parser.add_argument("--self-test", action="store_true", help="each check must detect the loss of its own evidence")
    parser.add_argument("--run-node", metavar="JSON", help=argparse.SUPPRESS)
    parser.add_argument(
        "--json", action="store_true",
        help="emit one machine-readable record per condition plus a top-level "
             "summary, instead of the human report (Issue #423-A / ADR-0063); "
             "the human report is unaffected when this flag is absent (AC-2)")
    args = parser.parse_args(argv)

    if args.run_node is not None:
        _run_node_worker(args.run_node)
        return 0

    if args.self_test:
        ok = run_self_test()
        return 0 if ok else 1

    manifest, manifest_error = load_manifest(REPO_ROOT)
    ctx = Ctx(REPO_ROOT, manifest, manifest_error)
    exit_ok = True
    manual_count = 0
    verdicts = []
    for check in REGISTRY:
        verdict = run_check_safely(check, ctx)
        if not args.json:
            # AC-2: byte-identical to the pre-#423-A report when --json is
            # absent -- this branch is the entire old body, untouched.
            print(verdict.line(check.id))
        if verdict.status == Status.MANUAL:
            manual_count += 1
        elif verdict.status != Status.PASS:
            exit_ok = False
        verdicts.append((check.id, verdict))
    if manual_count > len(MANUAL_CONDITIONS):
        print(f"FATAL: {manual_count} MANUAL verdicts printed, only {len(MANUAL_CONDITIONS)} are permitted", file=sys.stderr)
        exit_ok = False

    if args.json:
        # AC-1/AC-7: `manifest_readable` is reported explicitly rather than
        # letting an unreadable manifest quietly present as "24 generic
        # FAILs" -- a consumer can tell "the gate itself is broken" apart
        # from "the work genuinely isn't done" without re-deriving it from
        # 24 free-text reasons.
        records = []
        unallocatable = []
        for check_id, verdict in verdicts:
            artifact = _blocking_artifact_for(check_id, verdict)
            records.append({
                "id": check_id,
                "verdict": verdict.status,
                "reason": verdict.reason,
                "blocking_artifact": artifact,
            })
            # unallocatable (ADR-0063): every FAIL the allocator cannot
            # auto-diagnose (no derivable artifact) plus every BLOCKED/MANUAL
            # -- both are conditions no task can be usefully linked to yet.
            if verdict.status in (Status.BLOCKED, Status.MANUAL):
                unallocatable.append(check_id)
            elif verdict.status == Status.FAIL and artifact is None:
                unallocatable.append(check_id)
        payload = {
            "schema_version": DOD_JSON_SCHEMA_VERSION,
            "manifest_readable": manifest is not None,
            "dod_complete": all(v.status == Status.PASS for _, v in verdicts),
            "unallocatable": unallocatable,
            "conditions": records,
        }
        print(json.dumps(payload, sort_keys=True))

    return 0 if exit_ok else 1


if __name__ == "__main__":
    sys.exit(main())
