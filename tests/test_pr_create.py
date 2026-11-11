"""The sanctioned PR path: `nightshift-ctl pr-create -- <gh args>` lints the
body's ACTUAL bytes, then execs gh — closing the --body-file hole where a
raw `gh pr create` could carry an unlinted file.

gh is stubbed via NIGHTSHIFT_CTL_GH so no network or auth is involved: the stub
records its argv, which proves gh was (or was not) invoked.
"""

import json
import os
import stat
import subprocess
import tempfile
import unittest

from ctlutil import CTL


class PrCreate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pr-create-")
        self.marker = os.path.join(self.dir, "gh-argv.json")
        self.gh = os.path.join(self.dir, "gh-stub")
        with open(self.gh, "w") as f:
            f.write("#!/usr/bin/env python3\n"
                    "import json, sys\n"
                    f"json.dump(sys.argv[1:], open({self.marker!r}, 'w'))\n")
        os.chmod(self.gh, os.stat(self.gh).st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, NIGHTSHIFT_CTL_GH=self.gh)

    def run_pr(self, *argv):
        p = subprocess.run(
            [CTL, "--db", os.path.join(self.dir, "control.db"), "pr-create",
             "--"] + list(argv),
            capture_output=True, text=True, env=self.env)
        body = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
        return p.returncode, body

    def gh_argv(self):
        if not os.path.exists(self.marker):
            return None
        with open(self.marker) as f:
            return json.load(f)

    def write_body(self, text):
        path = os.path.join(self.dir, "body.md")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_bad_body_file_blocked_gh_never_called(self):
        path = self.write_body("Task PR part 1.\n\nCloses #12\n")
        code, body = self.run_pr("--title", "x", "--body-file", path)
        self.assertEqual(code, 3)
        self.assertTrue(any("closing keyword" in p for p in body["problems"]))
        self.assertIsNone(self.gh_argv())

    def test_missing_refs_blocked(self):
        path = self.write_body("A body with no issue reference.\n")
        code, body = self.run_pr("--title", "x", "--body-file", path)
        self.assertEqual(code, 3)
        self.assertIsNone(self.gh_argv())

    def test_good_body_file_execs_gh_with_same_args(self):
        path = self.write_body("Part 1 of 3.\n\nRefs #12\nADR: ADR-0007\n")
        code, _ = self.run_pr("--title", "x", "--body-file", path)
        self.assertEqual(code, 0)
        self.assertEqual(self.gh_argv(),
                         ["pr", "create", "--title", "x", "--body-file", path])

    def test_inline_body_supported(self):
        code, _ = self.run_pr("--title", "x", "--body", "Refs #9")
        self.assertEqual(code, 0)
        self.assertEqual(self.gh_argv()[-1], "Refs #9")

    def test_fill_rejected(self):
        code, body = self.run_pr("--title", "x", "--fill")
        self.assertEqual(code, 3)
        self.assertIsNone(self.gh_argv())

    def test_no_body_rejected(self):
        code, body = self.run_pr("--title", "x")
        self.assertEqual(code, 3)
        self.assertIsNone(self.gh_argv())

    def test_attached_shorthand_bodies_are_linted_as_gh_reads_them(self):
        """Audit low: the scan matched whole tokens only, so gh's pflag forms
        carried an unlinted body straight through."""
        bad = self.write_body("Closes #5\n")
        for argv in (["-bCloses #5"], ["-b=Closes #5"], ["-dbCloses #5"],
                     ["-db", "Closes #5"], ["--body=Closes #5"],
                     ["-F" + bad], ["-F=" + bad], ["-dF" + bad],
                     ["--body", "Refs #5", "--body-file", bad],
                     ["--body-file", bad, "--body", "Refs #5"]):
            with self.subTest(argv=argv):
                code, body = self.run_pr("--title", "x", *argv)
                self.assertEqual(code, 3, body)
                self.assertIsNone(self.gh_argv())

    def test_attached_shorthand_good_body_still_passes(self):
        good = self.write_body("Refs #12\n")
        for argv in (["-bRefs #12"], ["-dF" + good], ["-F=" + good]):
            with self.subTest(argv=argv):
                code, body = self.run_pr("-t", "x", *argv)
                self.assertEqual(code, 0, body)
                self.assertEqual(self.gh_argv(), ["pr", "create", "-t", "x", *argv])
                os.unlink(self.marker)

    def test_unknown_and_unlintable_flags_are_refused(self):
        for argv in (["--bodyfile", "x", "-b", "Refs #1"], ["-Z", "-b", "Refs #1"],
                     ["-e", "-b", "Refs #1"], ["--web", "-b", "Refs #1"],
                     ["--recover", "f", "-b", "Refs #1"], ["-fb", "Refs #1"],
                     ["-b", "Refs #1", "--", "extra"], ["-F", "-"]):
            with self.subTest(argv=argv):
                code, body = self.run_pr("--title", "x", *argv)
                self.assertEqual(code, 3, body)
                self.assertIsNone(self.gh_argv())


if __name__ == "__main__":
    unittest.main()
