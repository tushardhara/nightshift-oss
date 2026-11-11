"""Pin the one compatibility-safe policy value needed by the cutover."""

import os
import re
import unittest

from ctlutil import REPO


SPEC_ROOT = os.environ.get("NIGHTSHIFT_SPEC_ROOT") or os.path.join(REPO, "program")
GATE_PUSHES = re.compile(r"^\s*gate_pushes\s*:\s*(\S+)", re.MULTILINE)


def spec_files():
    """Return every YAML Program spec, refusing a vacuous empty corpus."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(SPEC_ROOT):
        for name in filenames:
            if name.endswith((".yaml", ".yml")):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


class GatePushesCompatibility(unittest.TestCase):
    """#129: unattended delivery must not park every push for one day."""

    def test_bundle_exists(self):
        self.assertTrue(spec_files(), f"no Program specs found below {SPEC_ROOT}")

    def test_root_declares_exactly_one_false_value(self):
        root = os.path.join(SPEC_ROOT, "config.yaml")
        with open(root, encoding="utf-8") as handle:
            values = GATE_PUSHES.findall(handle.read())
        self.assertEqual(
            values,
            ["false"],
            "gate_pushes=true turns ordinary push/merge into an unattended "
            "24-hour ASK; the reviewed bundle must declare false exactly once",
        )

    def test_no_seat_reintroduces_true(self):
        offenders = []
        for path in spec_files():
            with open(path, encoding="utf-8") as handle:
                values = GATE_PUSHES.findall(handle.read())
            if any(value != "false" for value in values):
                offenders.append(os.path.relpath(path, SPEC_ROOT))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
