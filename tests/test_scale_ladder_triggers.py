"""Issue #400 acceptance conditions 19 and 20, verbatim:

    19. "Numeric capacity and latency triggers govern any move to Redis, a
        broker, dedicated vector storage, separated services, or higher
        infrastructure tiers."
    20. "The design remains a modular monolith at the initial measured
        load."

`docs/architecture/SCALE-LADDER.md` is the artifact condition 19 names.
This suite parses its own markdown table (no separate machine-readable
copy exists -- the table IS the source of truth) and asserts every row's
"Switch when" cell carries at least one real number, never a feeling or a
date. Condition 20 is checked by scanning every real file under `bin/`
(not merely `bin/*.py` -- `bin/nightshift-ctl` and `bin/nightshift-release`
are Python scripts with no `.py` suffix, and a naive glob would silently
skip both) for a tier-1 dependency import.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LADDER = REPO / "docs" / "architecture" / "SCALE-LADDER.md"
BIN = REPO / "bin"

TIER1_DEPENDENCIES = ("redis", "kafka", "psycopg2", "psycopg", "pgvector",
                       "celery", "kubernetes")

_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(" + "|".join(TIER1_DEPENDENCIES) + r")\b"
    r"|from\s+(" + "|".join(TIER1_DEPENDENCIES) + r")\b)",
    re.MULTILINE,
)


def _parse_ladder_rows():
    """Every data row of the single markdown table in SCALE-LADDER.md, as
    a list of cell strings. Assumes GitHub-flavored pipe tables (the only
    kind this repo's docs use) and skips the header/separator rows."""
    text = LADDER.read_text(encoding="utf-8")
    rows = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            continue
        if re.fullmatch(r"\|[\s:|-]+\|", stripped):
            in_table = True  # the header separator row -- table starts after it
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if in_table:
            rows.append(cells)
        # the header row itself (before the separator) is never a data row
    return rows


class EveryLadderRowHasANumericTrigger(unittest.TestCase):

    def test_the_table_has_rows(self):
        rows = _parse_ladder_rows()
        self.assertGreaterEqual(len(rows), 5, "SCALE-LADDER.md's table looks empty or unparsable")

    def test_every_row_switch_when_cell_carries_a_real_number(self):
        rows = _parse_ladder_rows()
        header = ["Concern", "Tier 0 — build this", "Tier 1", "Tier 2", "Switch when", "Now at"]
        switch_when_index = 4
        for row in rows:
            concern = row[0]
            with self.subTest(concern=concern):
                self.assertGreater(len(row), switch_when_index, f"row for {concern!r} is malformed")
                cell = row[switch_when_index]
                self.assertTrue(cell, f"{concern!r} has an empty 'Switch when' cell")
                self.assertRegex(
                    cell, r"\d",
                    f"{concern!r}'s 'Switch when' cell has no number: {cell!r}")


def _scan_dir_for_tier1_imports(bin_dir):
    """The pure scanning logic, factored out so the mutation-evidence test
    below can feed it a synthetic directory instead of touching the real
    bin/ tree."""
    offenders = []
    for path in sorted(Path(bin_dir).iterdir()):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in _IMPORT_RE.finditer(text):
            dep = match.group(1) or match.group(2)
            offenders.append((path.name, dep))
    return offenders


class NoTierOneDependencyIsImported(unittest.TestCase):

    def test_no_bin_file_imports_a_tier_one_dependency(self):
        """Scans every REGULAR file under bin/, not a `*.py` glob --
        bin/nightshift-ctl and bin/nightshift-release are real Python
        scripts with no .py extension, and a naive glob silently misses
        them."""
        offenders = _scan_dir_for_tier1_imports(BIN)
        self.assertEqual(offenders, [], f"tier-1 dependency imported under bin/: {offenders}")

    def test_the_detector_catches_a_real_import_added_to_a_synthetic_bin_dir(self):
        """Mutation-evidence proof for the scanner itself: write a file
        with a real `import redis` line into a SYNTHETIC directory (never
        the real bin/) and confirm the detector names it. Matches the AC's
        own defect shape ('add `import redis` to any bin/ module')
        without risking a real production file."""
        import shutil
        import tempfile
        scratch = tempfile.mkdtemp(prefix="nightshift-ladder-test-")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        offending = Path(scratch) / "nightshift_fake_module.py"
        offending.write_text("#!/usr/bin/env python3\nimport os\nimport redis\n")
        clean = Path(scratch) / "nightshift_clean_module.py"
        clean.write_text("#!/usr/bin/env python3\nimport os\nimport sys\n")

        offenders = _scan_dir_for_tier1_imports(scratch)
        self.assertEqual(offenders, [("nightshift_fake_module.py", "redis")])

    def test_the_detector_itself_catches_every_named_import_shape(self):
        """Sanity check for the regex above, independent of today's real
        bin/ contents: it must actually match the import shapes it claims
        to catch, for every one of the six named tier-1 dependencies."""
        for dep in TIER1_DEPENDENCIES:
            with self.subTest(dep=dep, form="import"):
                self.assertIsNotNone(_IMPORT_RE.search(f"import {dep}"), dep)
            with self.subTest(dep=dep, form="from"):
                self.assertIsNotNone(_IMPORT_RE.search(f"from {dep} import x"), dep)
        self.assertIsNone(_IMPORT_RE.search("import re"))
        self.assertIsNone(_IMPORT_RE.search("import sys"))


if __name__ == "__main__":
    unittest.main()
