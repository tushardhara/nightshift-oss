#!/usr/bin/env python3
"""Suite driver — ADR-0041, issue #245.

Runs the full control-plane suite and audits the host-UID-ancestry skip
manifest, instead of aborting the whole run at a top-level precondition
check the way this fix originally shipped. That first version made the
right call fail loud, but it also hid every one of the suite's ~900
unrelated tests' regression evidence behind a single early exit whenever
the sandbox's uid remap fired — the exact objection ADR-0041 exists to
resolve.

`ProjectTrustedAncestry` (see host_uid_ancestry.py) is the only class that
assumes truthful host UID ancestry; it — and only it — skips, by exact test
id, when this process cannot see `/` as uid 0. Everything else in the suite
still runs and must still pass. GitHub Actions (or any other disposable,
credential-free runner with real UID visibility) still runs
`ProjectTrustedAncestry` unconditionally: this driver has no way to tell a
sandboxed local run apart from CI, so `release`'s own merge procedure is
what must additionally verify the GHA `tests` check is green for the exact
candidate SHA before treating a local skip here as sufficient on its own.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from host_uid_ancestry import (
    HOST_UID_REMAP_REASON_MARKER, HOST_UID_REMAP_TEST_PREFIX, uid_remapped)

TESTS_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = TESTS_DIR / "host-uid-skip-manifest.json"


def _load_manifest() -> dict:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    marker = data.get("reason_marker")
    if marker != HOST_UID_REMAP_REASON_MARKER:
        # Wired in, not decorative: a manifest and code that disagree on the
        # marker are not describing the same skip reason at all, so nothing
        # downstream (matching actual skips, matching test ids) can be
        # trusted -- fail immediately rather than silently auditing against
        # the wrong thing.
        raise SystemExit(
            f"{MANIFEST_PATH.name}'s reason_marker {marker!r} does not match "
            f"the code's HOST_UID_REMAP_REASON_MARKER {HOST_UID_REMAP_REASON_MARKER!r} "
            "-- manifest and code have drifted out of sync; regenerate the manifest.")
    return data


def _load_manifest_ids() -> set[str]:
    return set(_load_manifest()["tests"])


def _marked_skips(result: unittest.TestResult) -> set[str]:
    return {
        test.id() for test, reason in result.skipped
        if HOST_UID_REMAP_REASON_MARKER in reason
    }


def _flatten_ids(suite: unittest.TestSuite) -> set[str]:
    """Every individual test id a (possibly nested) suite actually selected.

    Needed so `-k PATTERN`/similar filtering doesn't trip the "manifest entry
    that did NOT skip" check for a manifest test that was never part of THIS
    invocation's selected set at all -- that's a different fact from "it ran
    and failed to skip."
    """
    ids: set[str] = set()
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            ids |= _flatten_ids(item)
        else:
            ids.add(item.id())
    return ids


def _all_test_ids() -> set[str]:
    """Every test id discoverable in the suite, ignoring any `-k`/similar
    filtering this invocation applied -- used ONLY to prove a manifest id
    corresponds to a real test, independent of what got selected to run.

    Without this, a manifest entry that matches nothing at all (a typo, or a
    test that was renamed or deleted without updating the manifest) is
    silently absent from BOTH `collected_ids` and `actual` and so never
    trips either half of the audit below -- reviewed finding on PR #249: a
    fabricated, nonexistent test id added to the manifest produced a clean
    `OK (skipped=12)` / exit 0 with no diagnostic at all.
    """
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS_DIR), pattern="test_*.py")
    return _flatten_ids(suite)


def audit(result: unittest.TestResult, manifest_ids: set[str], *,
          remapped: bool, collected_ids: set[str] | None = None,
          all_ids: set[str] | None = None) -> list[str]:
    """Return a list of problems; empty means the skip set is exactly right.

    `remapped` is required, not inferred from `result` -- the manifest only
    describes what's ALLOWED to skip when a remap is actually detected. A
    bare host or CI run has real uid 0, so `ProjectTrustedAncestry` runs
    normally there and must PASS, not skip; treating "on the manifest" as
    "must have skipped" regardless of `remapped` produced a real false
    positive on the bare host (every manifest entry legitimately ran and
    passed, and got flagged as a missing skip anyway) -- caught by actually
    running this against the real suite, not just the audit's own unit
    tests, which is why this parameter has no default.

    `collected_ids` scopes the "missing skip" half of the check (only
    meaningful when `remapped` is true) to manifest tests that were actually
    SELECTED this run -- with no `collected_ids` (e.g. a unit test driving
    `audit()` directly), every manifest id is treated as collected, which is
    the right default for a full, unfiltered run.

    `all_ids` is a DIFFERENT set from `collected_ids`: every test id that
    EXISTS in the suite at all, regardless of `-k`/similar filtering. A
    manifest id that matches nothing in `all_ids` can never appear in
    `collected_ids` or `actual` either, so without this check it is silently
    absent from every other comparison below and produces no diagnostic at
    all -- reviewed finding on PR #249. With no `all_ids` (e.g. a unit test),
    every manifest id is treated as real, which is the right default when
    dangling-reference detection isn't what's being exercised.
    """
    if collected_ids is None:
        collected_ids = manifest_ids
    if all_ids is None:
        all_ids = manifest_ids
    actual = _marked_skips(result)

    dangling = manifest_ids - all_ids
    off_scope_manifest = {
        i for i in manifest_ids if not i.startswith(HOST_UID_REMAP_TEST_PREFIX)}
    off_scope_actual = {
        i for i in actual if not i.startswith(HOST_UID_REMAP_TEST_PREFIX)}

    if remapped:
        unexpected = actual - manifest_ids
        missing = (manifest_ids & collected_ids) - actual
    else:
        # No remap detected: NOTHING should carry this skip reason -- every
        # manifest test should have run for real and passed.
        unexpected = actual
        missing = set()

    problems = []
    if dangling:
        problems.append(
            "manifest entries that do not correspond to any real, "
            f"discoverable test -- renamed, removed, or a typo: {sorted(dangling)}")
    if off_scope_manifest:
        problems.append(
            f"manifest entries outside {HOST_UID_REMAP_TEST_PREFIX!r} -- only "
            f"that class may carry this skip reason: {sorted(off_scope_manifest)}")
    if off_scope_actual:
        problems.append(
            f"test(s) outside {HOST_UID_REMAP_TEST_PREFIX!r} skipped with the "
            f"host-uid-remap reason -- only that class may use it: {sorted(off_scope_actual)}")
    if unexpected:
        problems.append(
            "unexpected host-uid-remap skip(s)"
            + ("" if remapped else " (no remap was even detected this run)")
            + f", not on the approved manifest ({MANIFEST_PATH.name}): "
            f"{sorted(unexpected)}")
    if missing:
        problems.append(
            "manifest entries that did NOT skip under a detected remap -- "
            "either the test was renamed/removed without updating the "
            f"manifest, or it no longer depends on host UID ancestry: {sorted(missing)}")
    return problems


class _CapturingProgram(unittest.TestProgram):
    """`TestProgram.__init__` builds the suite AND runs it in one shot, and
    `TestSuite.run()` clears each test out of the suite as it goes (a
    `_cleanup` memory optimisation) -- so the suite can only be inspected for
    "what got selected" before it runs, not after. Capture it right there."""

    collected_ids: set[str] = frozenset()

    def runTests(self):
        self.collected_ids = _flatten_ids(self.test)
        super().runTests()


SHARD_WEIGHTS_PATH = TESTS_DIR / "shard-weights.json"


def _module_of(test_id: str) -> str:
    """`pkg.module.Class.test_name` -> `pkg.module`.

    Sharding is by MODULE, never by individual test: a module's tests share
    `setUpClass` fixtures, and splitting one across runners would rebuild the
    same fixture twice and could interleave two processes over one temporary
    control plane.
    """
    parts = test_id.split(".")
    return ".".join(parts[:-2]) if len(parts) >= 3 else parts[0]


def _real_modules() -> frozenset[str]:
    """The module names that actually exist as `tests/test_*.py` files."""
    return frozenset(p.stem for p in TESTS_DIR.glob("test_*.py"))


def _modules_to_shard() -> list[str]:
    """Every discovered module, or death.

    `_module_of` reads a name out of a test id, and a test id is not always
    what it looks like. A module that raises on import does not yield
    `test_foo.Case.test_x`; unittest substitutes
    `unittest.loader._FailedTest.test_foo`, whose "module" is `unittest.loader`
    -- a name that loads an empty suite. Left alone, the broken module drops
    out of every bin and its import error is never raised by anyone: the build
    goes green BECAUSE a test file is broken. So any derived name that is not
    a real `tests/test_*.py` file stops the run here, loudly.
    """
    real = _real_modules()
    derived = {_module_of(i) for i in _all_test_ids()}
    bogus = sorted(derived - real)
    if bogus:
        raise SystemExit(
            "cannot shard: these are not importable test modules: "
            + ", ".join(bogus)
            + " -- run the suite unsharded to see the underlying error")
    missing = sorted(real - derived)
    if missing:
        raise SystemExit(
            "cannot shard: discovered no tests in " + ", ".join(missing))
    return sorted(derived)


def _weights() -> dict:
    """Measured seconds per module, for balancing. Missing is not fatal.

    Modules are wildly unequal -- the liveness and WAL tests sleep by design --
    so splitting by name or by test count leaves one runner carrying minutes
    the others do not. A module absent from the file is treated as the median,
    which keeps a newly added module from silently landing in whichever bin
    happens to be shortest.
    """
    try:
        import json as _json
        data = _json.loads(SHARD_WEIGHTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    modules = data.get("modules", data) if isinstance(data, dict) else {}
    # Only numbers survive: the file carries a comment and a total alongside
    # the measurements, and a stray string here would raise mid-partition --
    # turning an advisory balance hint into a hard failure of the whole suite.
    return {k: v for k, v in modules.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def partition(modules, shards: int) -> list:
    """Split module names into `shards` bins, longest-first.

    TOTAL and DISJOINT by construction: every module is appended to exactly
    one bin, and the bins are returned in full. That property is the whole
    safety argument for this feature -- a module that fell out of every bin
    would be a test nothing runs, which is the failure #729 exists to catch,
    arriving through the door marked "faster CI". It is asserted directly in
    tests/test_738_shard_partition.py for many shard counts, not just the one
    the workflow uses.

    Deterministic: same input, same output, on every runner. Bins are chosen
    by remaining load, ties broken by index, so no runner's assignment depends
    on filesystem order or the wall clock.
    """
    if shards < 1:
        raise ValueError("shards must be >= 1")
    weights = _weights()
    known = sorted(weights.values())
    median = known[len(known) // 2] if known else 1.0
    ordered = sorted(modules, key=lambda m: (-weights.get(m, median), m))
    bins = [[] for _ in range(shards)]
    load = [0.0] * shards
    for module in ordered:
        index = min(range(shards), key=lambda i: (load[i], i))
        bins[index].append(module)
        load[index] += weights.get(module, median)
    return [sorted(b) for b in bins]


def _parse_shard(argv: list[str]):
    """Pull `--shard K/N` out of argv before unittest sees it."""
    rest, spec = [], None
    iterator = iter(argv)
    for arg in iterator:
        if arg == "--shard":
            spec = next(iterator, None)
            if spec is None:
                raise SystemExit("--shard wants K/N, got nothing")
        elif arg.startswith("--shard="):
            spec = arg.split("=", 1)[1]
        else:
            rest.append(arg)
    if spec is None:
        return rest, None
    try:
        k, n = (int(part) for part in spec.split("/", 1))
    except ValueError:
        raise SystemExit(f"--shard wants K/N, got {spec!r}")
    if not (1 <= k <= n):
        raise SystemExit(f"--shard {spec}: K must be between 1 and N")
    return rest, (k, n)


def main(argv: list[str]) -> int:
    argv, shard = _parse_shard(argv)
    if shard is not None:
        k, n = shard
        modules = _modules_to_shard()
        mine = partition(modules, n)[k - 1]
        if not mine:
            # A shard with nothing in it is not an error -- more runners than
            # modules is a legitimate configuration -- but it must SAY so,
            # because "0 tests, OK" and "everything passed" print identically.
            print(f"shard {k}/{n}: no modules assigned "
                  f"({len(modules)} modules over {n} shards)")
            return 0
        print(f"shard {k}/{n}: {len(mine)} of {len(modules)} modules")
        # Named modules are passed to `unittest` directly. The risk here is
        # not that something outside the set is picked up -- it is that
        # something inside discovery never reaches the set; `_modules_to_shard`
        # is what refuses that.
        argv = [*argv, *mine]

    program = _CapturingProgram(
        module=None,
        argv=(["run_suite.py", "discover", "-s", str(TESTS_DIR),
               "-p", "test_*.py", "-v", *argv] if shard is None else
              ["run_suite.py", "-v", *argv]),
        exit=False,
    )
    result = program.result

    problems = audit(result, _load_manifest_ids(), remapped=uid_remapped(),
                      collected_ids=program.collected_ids,
                      all_ids=_all_test_ids())
    for problem in problems:
        print(f"FATAL: {problem}", file=sys.stderr)

    return 0 if (result.wasSuccessful() and not problems) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
