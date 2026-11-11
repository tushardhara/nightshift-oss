"""ADR citation integrity -- guards against a code comment citing an ADR
rule or section that does not exist in the ADR it names.

This is the third integrity failure of its kind found in this repo
(fabricated test names in an acceptance table; a projection naming an
authority nobody declared; and the defect this suite pins:
`bin/nightshift_conformance.py`'s `languages_of()` cited "ADR-0086's
greenfield rule" -- a rule ADR-0086 never recorded -- while the docstring's
own gloss had also gone stale against a later commit that changed absent-
field handling from permissive to DENY). Two more of the same shape were
found alongside it during this stream's own scan (`bin/nightshift_provenance.py`
citing the wrong ADR number for a real rule; `bin/nightshift-ctl` citing a
rule by a name the ADR never uses, while correctly citing the same rule by
section two lines above). All three are fixed in this same change; this
suite is what stops a fourth.

Five groups:
  * `RepoWideCitationIntegrity` -- runs the real scan
    (`tests/adr_citation_guard.py`) against this repository's actual
    `bin/` + `program/` tree and `docs/decisions/` ADRs.
  * `FixtureCorpusDetection` -- the unit-level mutation evidence: a
    synthetic, non-git temp corpus proving each check kind actually
    detects what it claims to (including a citation whose token wraps
    across a line, in a plain docstring and in a `#` comment
    continuation), plus over-strictness controls proving a genuinely
    correct citation -- wrapped or not -- is left alone.
  * `LanguagesOfDocstringRegression` -- pins the specific defect: the
    fabricated citation is gone, and the docstring states the real (DENY)
    consequence rather than the old, backwards "does not bind" gloss.
  * `Adr0086RecordsTheAbsentBaseFieldRule` -- the docstring's new citation
    is backed by an actual decision in ADR-0086, not a second fabrication.
  * `MemoryStateRootModeCitationRegression` -- pins a FOURTH fabricated
    citation, found only once the line-wrap fix above made it checkable:
    `nightshift_memory.py`'s `resolve_memory_root()` cited "ADR-0021's own
    state_root requirement" for a rule that is actually ADR-0020 §2's.

See `tests/adr_citation_guard.py`'s module docstring for exactly which
citation shapes are checked and which are deliberately left unchecked.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
TESTS_DIR = Path(__file__).resolve().parent

# Direct import, no try/except: if this module fails to import, the whole
# test file must error loudly at collection, not skip silently (no
# fail-soft test loaders).
sys.path.insert(0, str(TESTS_DIR))
import adr_citation_guard as guard  # noqa: E402


def _write(root: Path, rel_path: str, content: str) -> None:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ===========================================================================
# Repo-wide: the guard as CI will actually run it.
# ===========================================================================

class RepoWideCitationIntegrity(unittest.TestCase):
    """Scans the real bin/ + program/ tree. All three check kinds must come
    back clean, and the scan itself must not be vacuous."""

    @classmethod
    def setUpClass(cls):
        cls.problems, cls.checked = guard.scan()

    def test_every_adr_reference_in_bin_and_program_resolves_to_an_existing_adr_file(self):
        hits = [p for p in self.problems if p.startswith("existence:")]
        self.assertEqual(hits, [], "unresolved bare ADR reference(s):\n" + "\n".join(hits))

    def test_no_possessive_rule_citation_names_a_token_absent_from_the_cited_adr(self):
        hits = [p for p in self.problems if p.startswith("possessive:")]
        self.assertEqual(
            hits, [], "fabricated ADR rule citation(s):\n" + "\n".join(hits))

    def test_no_section_citation_names_a_numbered_item_the_cited_adr_does_not_have(self):
        hits = [p for p in self.problems if p.startswith("section:")]
        self.assertEqual(
            hits, [], "ADR section citation(s) naming a nonexistent item:\n"
            + "\n".join(hits))

    def test_the_repo_scan_checks_a_nonzero_number_of_citations_including_a_known_good_one(self):
        # A scan that extracted nothing (broken regex, wrong roots, an
        # empty `git ls-files`) would report zero problems for the wrong
        # reason -- zero inputs, not zero defects. This is the exact
        # false-negative shape ("0 findings" because "0 checked") this
        # repo has already been burned by.
        self.assertGreater(
            len(self.checked), 0,
            "scan checked nothing -- a clean run here proves nothing")
        known_good_section = {
            "file": "bin/nightshift-ctl", "adr": "0002", "kind": "section", "detail": "3"}
        known_good_possessive = {
            "file": "bin/nightshift-ctl", "adr": "0061", "kind": "possessive",
            "detail": "Decision"}
        self.assertIn(known_good_section, self.checked)
        self.assertIn(known_good_possessive, self.checked)
        # `known_good_possessive`'s token, "Decision", is a heading present
        # in nearly every ADR -- a resolver replaced by `return True` would
        # still pass it. This second canary's token is specific to ONE ADR
        # ("absent-base-field" names ADR-0086's rule and nothing else's),
        # so a stubbed-out resolver fails this half even if it fakes the
        # generic-token half above.
        known_good_specific_possessive = {
            "file": "bin/nightshift_conformance.py", "adr": "0086",
            "kind": "possessive", "detail": "absent-base-field"}
        self.assertIn(known_good_specific_possessive, self.checked)


# ===========================================================================
# Fixture corpus: unit-level mutation evidence + an over-strictness control.
# ===========================================================================

class FixtureCorpusDetection(unittest.TestCase):
    """Same `scan()` logic, pointed at a synthetic temp directory via
    `walk_files` (no git repo needed or used -- #698). One fixture ADR with
    two real numbered decision items and two real rule phrases (one
    single-word, one multi-word so it CAN be split by a line wrap); eight
    citing files -- four single-line (three bad in a different way each,
    one entirely correct) and four that wrap the same good/bad pairing
    across a line break, both in a plain multi-line docstring and across a
    `#` comment continuation."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="adr_citation_fixture_")
        root = Path(cls.tmp)

        _write(root, "docs/decisions/ADR-0001-example-decision.md", (
            "# ADR-0001: Example decision\n\n"
            "Date: 2026-01-01 -- Status: **accepted**\n\n"
            "## Decision\n\n"
            "1. **First real item.** Widgets must be blue.\n"
            "2. **Second real item.** Frobnicate before dispatch.\n\n"
            "## Consequences\n\n"
            "The frobnicate-before-dispatch rule prevents stale widgets "
            "from shipping. Retries use exponential backoff before giving "
            "up.\n"
        ))

        _write(root, "bin/good.py", (
            "# ADR-0001's frobnicate-before-dispatch rule applies here.\n"
            "# see ADR-0001 section 1 for the widget requirement.\n"
            "def widget():\n    pass\n"
        ))

        _write(root, "bin/bad_existence.py", (
            "# per ADR-0999, nothing backs this up.\n"
            "def orphan():\n    pass\n"
        ))

        _write(root, "bin/bad_section.py", (
            "# ADR-0001 section 5 says nothing at all.\n"
            "def nowhere():\n    pass\n"
        ))

        _write(root, "bin/bad_possessive.py", (
            "# ADR-0001's teleportation rule is honored here.\n"
            "def teleport():\n    pass\n"
        ))

        # Same good/bad possessive pairing as above, but the token itself
        # -- not just the whitespace around it -- is split across a line
        # break, in the two ways the real tree's `ADR-0021's own\n    state_
        # root requirement` fell through: a plain multi-line docstring, and
        # a Python comment continued on a fresh `#` line.
        _write(root, "bin/good_wrapped_docstring.py", (
            "def retry():\n"
            '    """Applies ADR-0001\'s exponential\n'
            '    backoff rule before giving up."""\n'
            "    pass\n"
        ))

        _write(root, "bin/good_wrapped_hash.py", (
            "# Uses ADR-0001's exponential\n"
            "# backoff rule before giving up.\n"
            "def retry_again():\n    pass\n"
        ))

        _write(root, "bin/bad_wrapped_docstring.py", (
            "def teleport_slow():\n"
            '    """Applies ADR-0001\'s made up\n'
            '    requirement here."""\n'
            "    pass\n"
        ))

        _write(root, "bin/bad_wrapped_hash.py", (
            "# An absent case is exempt here per ADR-0001's\n"
            "# teleportation rule.\n"
            "def teleport_again():\n    pass\n"
        ))

        cls.problems, cls.checked = guard.scan(
            ("bin", "program"), repo_root=root, list_files=guard.walk_files)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_reference_to_an_adr_number_with_no_file_is_reported(self):
        hits = [p for p in self.problems
                if p.startswith("existence:") and "bad_existence.py" in p]
        self.assertEqual(len(hits), 1, self.problems)
        self.assertIn("ADR-0999", hits[0])

    def test_a_section_number_the_adr_lacks_is_reported(self):
        hits = [p for p in self.problems
                if p.startswith("section:") and "bad_section.py" in p]
        self.assertEqual(len(hits), 1, self.problems)
        self.assertIn("ADR-0001", hits[0])
        self.assertIn("5", hits[0])

    def test_a_possessive_token_absent_from_the_adr_is_reported(self):
        hits = [p for p in self.problems
                if p.startswith("possessive:") and "bad_possessive.py" in p]
        self.assertEqual(len(hits), 1, self.problems)
        self.assertIn("teleportation", hits[0])

    def test_a_real_rule_token_and_a_real_numbered_item_are_not_reported(self):
        """Over-strictness control: `good.py` cites a token that really
        appears in the ADR and a section number that really is a numbered
        item there. Tightening the resolver to require, say, a heading-only
        token match or a `### N.`-only section match would fail this test
        -- and would also fail on the real tree's ADR-0002 §3 / ADR-0061
        'Decision' pair."""
        hits = [p for p in self.problems if "good.py" in p]
        self.assertEqual(hits, [])

    def test_a_possessive_token_wrapped_mid_token_across_a_docstring_line_is_reported(self):
        """The real-tree defect this guard closed: a possessive citation
        whose token is split across a line break by ordinary text wrapping
        (no `#`), the way `nightshift_memory.py`'s docstring split 'own
        state_root' across two lines. Before `_prepare_source_for_scan`,
        `bad_wrapped_docstring.py` was invisible to the scan entirely --
        not reported AND not in `checked` -- which is the false-negative
        shape this test pins."""
        hits = [p for p in self.problems
                if p.startswith("possessive:") and "bad_wrapped_docstring.py" in p]
        self.assertEqual(len(hits), 1, self.problems)
        self.assertIn("made up", hits[0])

    def test_a_possessive_token_wrapped_across_a_hash_comment_continuation_is_reported(self):
        """Same defect shape, but wrapped the way the verifier's own
        mutation exploited it: two consecutive `#`-prefixed comment lines,
        no docstring involved."""
        hits = [p for p in self.problems
                if p.startswith("possessive:") and "bad_wrapped_hash.py" in p]
        self.assertEqual(len(hits), 1, self.problems)
        self.assertIn("teleportation", hits[0])

    def test_a_real_multi_word_token_wrapped_across_a_docstring_line_is_not_reported(self):
        """Over-strictness control for the docstring-wrap case: the token
        really does appear (once whitespace is collapsed) in the ADR, so
        joining the wrap must not turn a correct citation into a false
        positive."""
        hits = [p for p in self.problems if "good_wrapped_docstring.py" in p]
        self.assertEqual(hits, [])

    def test_a_real_multi_word_token_wrapped_across_a_hash_comment_is_not_reported(self):
        """Over-strictness control for the `#`-comment-wrap case."""
        hits = [p for p in self.problems if "good_wrapped_hash.py" in p]
        self.assertEqual(hits, [])

    def test_the_fixture_scan_itself_checked_something_from_every_file(self):
        files_checked = {c["file"] for c in self.checked}
        self.assertEqual(
            files_checked,
            {"bin/good.py", "bin/bad_existence.py", "bin/bad_section.py",
             "bin/bad_possessive.py", "bin/good_wrapped_docstring.py",
             "bin/good_wrapped_hash.py", "bin/bad_wrapped_docstring.py",
             "bin/bad_wrapped_hash.py"})


# ===========================================================================
# Regression: the specific defect this stream fixes.
# ===========================================================================

class LanguagesOfDocstringRegression(unittest.TestCase):
    """Pins `bin/nightshift_conformance.py`'s `languages_of()` docstring:
    no fabricated 'greenfield rule', and the real DENY consequence of an
    absent base field is stated instead of the old, backwards 'an empty
    Brain does not bind' gloss."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        pycache = BIN / "__pycache__"
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
        sys.path.insert(0, str(BIN))
        import nightshift_conformance as gate_module  # noqa: E402
        cls.doc = (gate_module.languages_of.__doc__ or "").casefold()

    def test_languages_of_docstring_no_longer_claims_a_greenfield_rule(self):
        self.assertNotIn("greenfield", self.doc)

    def test_languages_of_docstring_says_an_absent_base_field_denies(self):
        self.assertNotIn("does not bind", self.doc)
        self.assertIn("absent-base-field", self.doc)
        self.assertIn("deni", self.doc)  # covers "denies"/"denying"


# ===========================================================================
# The new citation is backed by an actual decision, not a second fabrication.
# ===========================================================================

class Adr0086RecordsTheAbsentBaseFieldRule(unittest.TestCase):
    """`languages_of()` now cites 'ADR-0086's absent-base-field rule' --
    this asserts ADR-0086 actually records that rule, checked through the
    same scanner path the repo-wide test uses, not a separate ad hoc string
    search."""

    def test_adr_0086_contains_the_absent_base_field_rule_the_module_cites(self):
        adr_files = list((REPO / "docs" / "decisions").glob("ADR-0086-*.md"))
        self.assertEqual(len(adr_files), 1, adr_files)
        content = adr_files[0].read_text(encoding="utf-8")
        self.assertIn("absent-base-field", content.casefold())

        problems, checked = guard.scan()
        entry = {"file": "bin/nightshift_conformance.py", "adr": "0086",
                 "kind": "possessive", "detail": "absent-base-field"}
        self.assertIn(entry, checked)
        self.assertEqual(
            [p for p in problems if "nightshift_conformance.py" in p], [])


# ===========================================================================
# Regression: the wrapped-citation gap found against this same stream's own
# fix, in a citation the line-wrap fix newly exposed as fabricated.
# ===========================================================================

class MemoryStateRootModeCitationRegression(unittest.TestCase):
    """`resolve_memory_root()` used to cite 'ADR-0021's own state_root
    requirement' for the mode-0700 directory it creates -- a citation
    `_prepare_source_for_scan` newly makes checkable (the token 'own
    state_root' was split across a docstring line wrap) and that, once
    checked, turned out to be the wrong ADR: the mode-0700 requirement for
    `state_root` itself is ADR-0020 §2's, not ADR-0021's (ADR-0021 narrows
    the ANCESTOR-trust question; it does not restate the final directory's
    own mode). The docstring now cites 'ADR-0020 section 2', and this pins
    both that the fabricated citation is gone and that ADR-0020 §2 really
    does contain the mode-0700 requirement it now cites."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        pycache = BIN / "__pycache__"
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
        sys.path.insert(0, str(BIN))
        import nightshift_memory as memory_module  # noqa: E402
        cls.doc = (memory_module.resolve_memory_root.__doc__ or "")

    def test_resolve_memory_root_docstring_no_longer_cites_adr_0021_for_the_mode(self):
        normalized = re.sub(r"\s+", " ", self.doc)
        self.assertNotIn("ADR-0021's own", normalized)

    def test_resolve_memory_root_docstring_cites_adr_0020_section_2_instead(self):
        normalized = re.sub(r"\s+", " ", self.doc)
        self.assertIn("ADR-0020 section 2", normalized)

    def test_adr_0020_section_2_actually_requires_mode_0700_for_state_root(self):
        adr_files = list((REPO / "docs" / "decisions").glob("ADR-0020-*.md"))
        self.assertEqual(len(adr_files), 1, adr_files)
        content = adr_files[0].read_text(encoding="utf-8")
        self.assertIn("### 2.", content)
        self.assertIn("have mode `0700`", content)

        problems, checked = guard.scan()
        section_entry = {"file": "bin/nightshift_memory.py", "adr": "0020",
                          "kind": "section", "detail": "2"}
        self.assertIn(section_entry, checked)
        self.assertEqual(
            [p for p in problems if "nightshift_memory.py" in p], [])


if __name__ == "__main__":
    unittest.main()
