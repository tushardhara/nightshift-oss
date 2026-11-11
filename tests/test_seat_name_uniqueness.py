"""#124 — the agent bundle in this repo must LOAD. Seat names are one namespace.

THE DEFECT. `crew_alpha` and `crew_beta` each declared six seats under the
same six names (`pm`, `qa`, `eng_claude`, `eng_codex`, `reviewer_claude`,
`reviewer_codex`). A bundle registers every seat into ONE flat namespace, so
those six names collide and the bundle does not load at all — not "loads with
the wrong crew's seat", it refuses. The running server does not have this
problem only because its copy was hand-edited to a `beta_*` parent-prefix
scheme that was never committed; ADR-0011 made the REPO bundle authoritative,
which turned an invisible drift into a P0.

WHY THIS FILE EXISTS AT ALL. The standing retro finding on this project is
`declared-but-not-enforced` — six instances and counting. "Seat names must be
unique" was true, was believed, and was written down nowhere a machine could
read. A control is not a control until something has observed it fire, so
this suite does two separate jobs:

  1. `RepoBundleTest` loads THE ACTUAL BUNDLE IN THIS REPO and fails on a
     duplicate. It was watched RED at 49b3d7f (six duplicates, listed by
     name) and GREEN after the rename; the capture is on the PR for #124.
  2. `DetectorFiresTest` builds synthetic bundles in a temp dir and proves
     the checker still distinguishes a colliding bundle from a clean one.
     Without it, (1) goes permanently green the moment it is fixed and
     nobody can ever see it fail again — a vacuous gate (cf. #64/RT-25).

PARSING. stdlib only, on purpose: CI has no pip install step (see
.github/workflows/tests.yml), so PyYAML is not available to lean on. A seat's
identity is its ONE top-level `name:` key, and top-level here means column 0
— every nested `name:` in these files lives inside an indented block scalar
(`prompt: |`) or mapping, so a column-0 scan cannot confuse the two. The
parser asserts exactly one such key per file rather than taking the first,
so a malformed spec fails loudly instead of being read half-right.
"""

import os
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(REPO, "program")


def seat_name(path):
    """The one top-level `name:` in a spec file.

    Raises rather than guessing: a spec with no name, or with two, is not a
    thing this checker may silently paper over — that is exactly how the
    declared-but-not-enforced pattern reproduces itself.
    """
    found = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.startswith("name:"):
                continue
            value = line[len("name:"):].split("#", 1)[0].strip()
            found.append((lineno, value.strip("'\"")))
    if len(found) != 1:
        raise AssertionError(
            "%s: expected exactly one top-level `name:`, found %d %r"
            % (path, len(found), [n for _, n in found]))
    lineno, value = found[0]
    if not value:
        raise AssertionError("%s:%d: `name:` is empty" % (path, lineno))
    return value


def load_bundle(root):
    """Every spec in the bundle, as [(repo-relative path, declared name)].

    Walks the tree the way the loader does — every `config.yaml` under the
    bundle root is a registered agent, whether it sits at the root, under
    `agents/<seat>/`, or under `agents/<crew>/agents/<seat>/`. Sorted so a
    failure message reads the same on every box.
    """
    specs = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if "config.yaml" in filenames:
            path = os.path.join(dirpath, "config.yaml")
            specs.append((os.path.relpath(path, root), seat_name(path)))
    specs.sort()
    return specs


def duplicates(specs):
    """{name: [paths]} for every name claimed by more than one spec."""
    by_name = {}
    for path, name in specs:
        by_name.setdefault(name, []).append(path)
    return {n: p for n, p in sorted(by_name.items()) if len(p) > 1}


def _write_spec(root, relpath, name):
    path = os.path.join(root, relpath, "config.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        # Shaped like a real seat spec: a block scalar whose body contains an
        # indented `name:` the parser must NOT pick up.
        f.write("spec_version: 1\n"
                "name: %s\n"
                "description: synthetic seat for the uniqueness detector\n"
                "prompt: |\n"
                "  Dispatch by name:\n"
                "    name: not-a-top-level-key\n" % name)
    return path


def _synthetic_bundle(tmp, names):
    """A bundle with a root, two crews, and `names` split between them."""
    root = os.path.join(tmp, "program")
    _write_spec(root, ".", "program")
    _write_spec(root, "agents/crew_a", "crew_a")
    _write_spec(root, "agents/crew_b", "crew_b")
    for crew, seats in names.items():
        for seat in seats:
            _write_spec(root, os.path.join("agents", crew, "agents", seat),
                        seat)
    return root


class RepoBundleTest(unittest.TestCase):
    """The gate. Points at the real `program/` tree in this checkout."""

    def setUp(self):
        self.specs = load_bundle(BUNDLE)

    def test_the_bundle_declares_no_duplicate_seat_names(self):
        """#124. RED at 49b3d7f on six names; GREEN after the `beta_*` rename.

        The message names every colliding path because the first thing a
        reader asks is "which crew's copy is the right one" — and answering
        that from a bare count costs a bisect.
        """
        dupes = duplicates(self.specs)
        self.assertEqual(
            dupes, {},
            "the bundle at %s does NOT load — %d seat name(s) are claimed by "
            "more than one spec:\n%s"
            % (BUNDLE, len(dupes),
               "\n".join("  %-24s %s" % (n, ", ".join(p))
                         for n, p in dupes.items())))

    def test_the_bundle_is_not_empty(self):
        """A checker that walks nothing reports no duplicates. Pin the floor
        so a moved directory shows up as a failure, not as a pass."""
        self.assertGreaterEqual(len(self.specs), 20, self.specs)

    def test_every_spec_declares_exactly_one_top_level_name(self):
        """`seat_name` raises on 0 or 2; walking the whole tree here is what
        turns that from a helper's private rule into an enforced one."""
        for relpath, name in self.specs:
            self.assertTrue(name, relpath)

    def test_a_seats_directory_matches_the_name_it_declares(self):
        """The prefix scheme only works if it is visible on disk. If
        `agents/beta_pm/` may declare `name: pm`, the rename buys nothing:
        the collision comes back at load time while the tree still looks
        fixed to a reviewer reading `ls`."""
        for relpath, name in self.specs:
            self.assertEqual(
                os.path.basename(os.path.dirname(relpath)) or "program", name,
                "%s declares `name: %s`" % (relpath, name))


class DetectorFiresTest(unittest.TestCase):
    """Proof the gate above is not vacuous, on bundles built here.

    Nothing in this class reads the repo — it must keep failing on a
    colliding bundle long after #124 is a closed issue.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="seat-names-test-")

    def test_it_fires_on_the_exact_shape_of_124(self):
        """Two crews, same six seat names — the state of the repo bundle at
        49b3d7f, reconstructed."""
        seats = ["pm", "qa", "eng_claude", "eng_codex",
                 "reviewer_claude", "reviewer_codex"]
        root = _synthetic_bundle(self.tmp, {"crew_a": seats, "crew_b": seats})
        dupes = duplicates(load_bundle(root))
        self.assertEqual(sorted(dupes), sorted(seats))
        for name in seats:
            self.assertEqual(len(dupes[name]), 2, dupes[name])

    def test_it_stays_quiet_on_a_clean_bundle(self):
        """The other half of the proof: a detector that always fires is as
        useless as one that never does."""
        root = _synthetic_bundle(self.tmp, {
            "crew_a": ["pm", "qa"],
            "crew_b": ["beta_pm", "beta_qa"],
        })
        self.assertEqual(duplicates(load_bundle(root)), {})

    def test_a_collision_between_a_crew_and_a_shared_function_is_caught(self):
        """The namespace is flat, so a seat can collide with a top-level
        function (`platform`, `release`, `oncall`) and not only with the
        other crew. Nothing about the `beta_*` scheme prevents that."""
        root = _synthetic_bundle(self.tmp, {"crew_a": ["release"]})
        _write_spec(root, "agents/release", "release")
        self.assertIn("release", duplicates(load_bundle(root)))

    def test_the_parser_ignores_name_keys_inside_block_scalars(self):
        """Every real seat spec has an indented `name:` in its prompt. If
        those counted, the parser would raise on the whole bundle and the
        gate would fail for a reason that has nothing to do with #124."""
        path = _write_spec(self.tmp, "solo", "beta_pm")
        self.assertEqual(seat_name(path), "beta_pm")

    def test_a_spec_with_no_name_is_an_error_not_a_pass(self):
        path = os.path.join(self.tmp, "nameless", "config.yaml")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("spec_version: 1\ndescription: no name here\n")
        with self.assertRaises(AssertionError):
            seat_name(path)

    def test_two_top_level_names_in_one_spec_is_an_error_not_a_pass(self):
        path = _write_spec(self.tmp, "twice", "beta_pm")
        with open(path, "a", encoding="utf-8") as f:
            f.write("name: pm\n")
        with self.assertRaises(AssertionError):
            seat_name(path)


if __name__ == "__main__":
    unittest.main()
