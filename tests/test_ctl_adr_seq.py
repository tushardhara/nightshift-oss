"""Issue #619 / ADR-0089: ADR numbers become a monotone control-plane sequence.

ADR numbers were allocated FROM MEMORY -- a handover file carrying a
`next ADR: NNNN` line. That control failed the way memory-based controls fail:
silently, with a result that looks correct. A spec cited ADR-0073 meaning
ADR-0074; then a real, unrelated `ADR-0073-*` landed, and the stale citations
stopped DANGLING and started RESOLVING -- to a document with an Option A, a
rejected Option B, and decisions 1 and 2 (#651). It structurally corroborates
while substantively contradicting, which is strictly worse than a dangling
reference, because dangling fails loudly.

ADR-0089 splits the remedy in two, and this file is where the second half
lives:

  REFUSE -- `seq next --name adr`, in the database, touching no disk. Monotone,
    so exactly one number transitions from vacant to filled per allocation, at
    a point above everything ever cited. It is blind to hand-picked numbers by
    construction and ADR-0089 says so.
  DETECT -- a hermetic scan of the checked-out `docs/decisions/` for duplicate
    ids. On a `pull_request` event that checkout IS the merge commit, which is
    the artifact `git merge-tree` proved merges clean today with two
    `ADR-0069-*` blobs, no conflict, and no test to catch it.

WA-008 -- WHICH TESTS COULD HAVE COME OUT DIFFERENTLY
=====================================================
Every test below is labelled in its own docstring with one of three tags. The
suite honours `$NIGHTSHIFT_TEST_CTL` (through `ctlutil.CTL`), so RED-ON-BASE
can be watched failing:

    mkdir -p "$PWD/.base" && git show 5eb1430:bin/nightshift-ctl > .base/ctl
    chmod +x .base/ctl
    NIGHTSHIFT_TEST_CTL="$PWD/.base/ctl" python3 -m unittest tests.test_ctl_adr_seq

EXTRACT IT SOMEWHERE PRIVATE, NOT /tmp. Python puts a script's own directory
on `sys.path[0]`, so a base binary run out of a shared `/tmp` imports whatever
`.py` debris other sessions have left there. Measured on this box: the SAME
bytes emitted 1 line of stdout from a private directory and 12 from `/tmp`,
because something in `/tmp` printed on import. `ctlutil` parses the LAST line
of stdout, so the suite still passed -- but the extra output changed which
assertions raised, and the base-binary result read `5 failures + 1 error` from
a private directory and `3 failures + 3 errors` from `/tmp`. Same six tests red
either way; only the split moved. The private-directory number is the real one.

  [RED-ON-BASE]  Exercises behaviour this change introduces. Fails against the
                 base binary. These, and only these, are evidence FOR the
                 change.
  [CHARACTERIZATION]  Passes against the base binary. `seq next --name adr`
                 already worked before this change -- that is the finding, not
                 a defect -- and these pin the behaviour ADR numbering is about
                 to depend on, which was previously unprotected. They are
                 evidence that nothing BROKE. They are not evidence that
                 anything was FIXED and must never be counted as such.
  [NEW-GUARD]    The duplicate-id checker. It asserts a property of the
                 REPOSITORY, not of the binary, so swapping in the base binary
                 cannot make it fail -- it is green on base by construction and
                 padding it into the red count would be dishonest. Its capacity
                 to fail is demonstrated instead by the synthetic fixtures
                 below, which are the WA-008 control: they show exactly what
                 the checker reports when the property is false.

THE IDENTITY RULE, AND A CORRECTION TO ADR-0089
===============================================
ADR-0089 states the rule as: an ADR is a file in `docs/decisions/` whose H1
matches ``^# ADR-(\\d{4}):``. Taken literally that is WRONG, and it was
measured against the tree during implementation. Two H1 forms are in use, both
on `main` and at the base sha:

    # ADR-0055: Close-gateway disposition record      <- 54 files at base
    # ADR-0013 -- Deterministic exact-SHA cutover     <- 8 files at base
                                                         (a real em dash)

Requiring the colon would silently drop those 8 genuine ADRs (0013-0017, 0020,
0021, 0064) out of the uniqueness scan -- a hole in DETECT precisely where a
duplicate could then hide -- or force them onto the exemption list, which
ADR-0089's own reversal trigger 3 says means the checker is the defect once it
passes ~3 entries. So the rule implemented here is ADR-0089's INTENT (identity
is declared by the H1, never inferred from the filename) with the separator set
widened to the two forms that actually occur. Reported to the program rather
than worked around silently.

ADR-0089 was amended to match, and then amended again to NARROW what this file
accepted: the separator set is exactly `:` and EM DASH, the EN DASH `–` must
FAIL, and a candidate filename's slug is OPTIONAL so a bare `ADR-0090.md` cannot
escape the scan by not looking like a candidate. Both corrections run in the
direction of the spec, not of the code that happened to be written first.

The rule is not a filename regex, and that part of ADR-0089 is exactly right:
`origin/main` carries `ADR-0001-0003-staff-review.md`, whose H1 is
``# Staff review: ADR-0001, ADR-0002, ADR-0003``. It is NOT an ADR -- it is a
review OF three of them -- and under a naive ``ADR-(\\d{4})`` filename rule it
reads as a duplicate of `ADR-0001-stack-and-shape.md`, firing falsely on day
one.

Unrecognised H1 shapes are FAILED, not skipped. A file that silently drops out
of the scan is worse than one that fires falsely: it is a place a real
duplicate can live undetected, which is this issue's whole failure mode.
"""

import os
import re
import shutil
import tempfile
import unicodedata
import unittest

from ctlutil import REPO, Ctl, run_parallel

DECISIONS = os.path.join(REPO, "docs", "decisions")

# An ADR declares its own number in its H1. The separator set is EXACTLY two
# characters -- ":" (U+003A) and EM DASH (U+2014) -- both in live use on main;
# see the module docstring for the measurement.
#
# EN DASH (U+2013) is deliberately NOT here. It is the canonical near-miss:
# visually almost identical to the em dash and absent from every ADR on main,
# so it is exactly the shape fail-don't-skip exists to catch. An earlier draft
# of this file accepted it defensively; ADR-0089's rule is to tighten the code
# to the spec rather than widen the spec to the code, because a checker broader
# than its own spec is this ADR's failure mode aimed at its own mechanism.
# Pinned in both directions by `test_an_en_dash_separator_is_not_accepted` and
# `test_an_em_dash_is_still_accepted_alongside_it`.
#
# The number is `\d{4,}` -- four or MORE digits. It was `\d{4}` (#619 review,
# MB-3), which meant `ADR-10000-five.md` matched neither pattern and was not
# even a candidate: DETECT would have silently switched itself off the moment
# the allocator reached five digits. An allocator that disables its own
# duplicate check at a round number is this ADR's failure mode aimed at itself,
# on a timer. Zero-padding to four is still the convention; the counter is
# monotone and unbounded above, so the CHECK must be too.
ADR_H1 = re.compile(r"^#[ \t]+ADR-([0-9]{4,})[ \t]*[:—]")
ADR_FILENAME = re.compile(r"^ADR-([0-9]{4,})(-.+)?\.md$")

# ---------------------------------------------------------- the asymmetry --
#
# IDENTITY (the two patterns above) is STRICT. The GESTURE detector below is
# deliberately OVER-BROAD. That asymmetry is the invariant, and it replaces
# three successive attempts -- two of mine, one in ADR-0089 -- to ENUMERATE
# which shapes are safe to skip. Every enumeration was disproved within a
# round. The rule that survives is:
#
#     A file may reach `not_an_adr` only if the OVER-BROAD detector finds
#     nothing ADR-shaped in its name or (for Markdown) its first line. When
#     the two disagree -- it gestures at an ADR but fails identity -- the
#     scan is LOUD.
#
# Round-2 review found three shapes that gestured and were silent. All three
# were one defect: the gesture detector was not strictly broader than
# identity.
#
#   lowercase       `adr-0090-x.md` + `# adr-0090:`   -> not_an_adr
#   Unicode digits  `ADR-٠٠٩٠-unicode.md`             -> counted as a
#                   DISTINCT number, so it did not even register as a
#                   duplicate of ADR-0090
#   leading BOM     `\ufeff# ADR-0093:`                -> not_an_adr
#
# The Unicode case is the sharpest and it is why identity now spells its
# digits `[0-9]` rather than `\d`. Python's `\d` is UNICODE-AWARE: it matches
# ARABIC-INDIC DIGIT ZERO, and NFKC does NOT fold that character to ASCII
# (verified: `normalize("NFKC", "\u0660")` is unchanged). So `\d{4,}` happily
# read `ADR-٠٠٩٠` as the number "٠٠٩٠", a string that is not equal to "0090",
# and the two files sat in different `by_number` keys with `duplicates() ==
# {}`. A duplicate hidden behind a BOM or a lowercase `a` is #651's own shape
# -- a thing that looks right and is not -- reproduced inside the mechanism
# built to end it.
#
# The gesture detector therefore uses `\d` ON PURPOSE, precisely because it is
# broader than `[0-9]`. Anything Python is willing to call a digit gestures;
# only ASCII digits are identity; everything in between is loud.
#
# Characters that carry no width but do change what a regex anchored at `^`
# sees. Stripped before gesture matching so a BOM cannot hide a heading.
_INVISIBLE = frozenset("\ufeff\u200b\u200c\u200d\u2060\u00ad\u180e")


def _fold(text):
    """Normalise aggressively before looking for a gesture. Lossy ON PURPOSE.

    Only ever used by the gesture detector, never by identity: folding is
    what makes the detector broad, and identity must stay narrow or the
    asymmetry collapses. Drops invisible characters anywhere in the string,
    applies NFKC (which folds fullwidth `\uff21` to `A`), trims surrounding
    whitespace, and casefolds.
    """
    visible = "".join(c for c in text if c not in _INVISIBLE)
    return unicodedata.normalize("NFKC", visible).strip().casefold()


# `\d` here is Unicode-aware and that is the point -- see the block above.
GESTURE_IN_FILENAME = re.compile(r"^adr[^0-9a-z]{0,4}\d")
GESTURE_IN_H1 = re.compile(r"^#+[^0-9a-z]{0,4}adr[^0-9a-z]{0,4}\d")


def gestures_in_filename(basename):
    """Does this NAME look like it is reaching for an ADR number?"""
    return bool(GESTURE_IN_FILENAME.match(_fold(basename)))


def gestures_in_h1(first_line):
    """Does this FIRST LINE look like it is reaching for an ADR number?"""
    return bool(GESTURE_IN_H1.match(_fold(first_line)))


# Every bucket `_classify` can return. `not_an_adr` is the ONLY silent one, and
# it is reachable only when neither the filename nor the H1 gestures at a
# number at all -- `ADR-TEMPLATE.md`, or ordinary prose filed in this
# directory. Everything else lands somewhere a test can see it.
BUCKETS = ("counted", "mismatched", "unrecognised", "not_an_adr", "symlinked")

# Files named `ADR-NNNN[-slug].md` that are deliberately not ADRs. Keep this
# short and keep every entry justified: ADR-0089's reversal trigger 3 says that
# past roughly three entries the filename convention, not the checker, is the
# thing that needs fixing.
NON_ADR_EXEMPTIONS = {
    "ADR-0001-0003-staff-review.md":
        "A staff review OF ADR-0001/0002/0003, not an ADR. H1 is "
        "'# Staff review: ADR-0001, ADR-0002, ADR-0003'. Predates this "
        "convention and lives on main; renaming it is outside #619's lease.",
}

# Migrations keep their serialize-lease discipline; `adr` deliberately has no
# resource string, which is the point of the gate being optional.
MIG = "db/migrations/**"


def _classify(root, rel):
    """Classify ONE file into exactly one bucket. Total by construction.

    Every path through this function returns, and returns a name in
    `BUCKETS`. That is the point: the #619 round-1 review found four separate
    defects that were all one defect -- the scan fell through to "count it" or
    to nothing at all, so `notes.md` carrying `# ADR-0091:` was accepted, and
    a nested file and a five-digit file vanished with `mismatched: []` and
    `unrecognised: []`. Patching four cases would have left the fifth. A total
    function cannot silently drop a shape nobody anticipated; it can only put
    it in the wrong bucket, which a test can still see.

    Reading is deliberately narrow:

    * SYMLINKS are never followed (MB-4). A checked-in symlink made DETECT
      read content from outside `docs/decisions/` -- potentially outside the
      checkout entirely -- and accept it as an ADR. They are recorded, not
      resolved, so the check stays hermetic.
    * A non-`.md` file is classified WITHOUT being opened, so a binary
      dropped in this directory cannot break the scan.
    * A `.md` that cannot be decoded raises. That is deliberate: a loud
      failure is not a silent skip, and this whole round is about the
      difference.
    """
    full = os.path.join(root, rel)
    if os.path.islink(full):
        return "symlinked", rel
    base = os.path.basename(rel)
    name_gestures = gestures_in_filename(base)
    if not base.endswith(".md"):
        # Cannot be an ADR (ADRs are Markdown), but an ADR-shaped name here is
        # still worth surfacing rather than ignoring.
        return ("unrecognised" if name_gestures else "not_an_adr"), rel
    with open(full, encoding="utf-8") as handle:
        first_line = handle.readline().rstrip("\n")
    named = ADR_FILENAME.match(base)
    declared = ADR_H1.match(first_line)
    filename_number = named.group(1) if named else None
    h1_number = declared.group(1) if declared else None
    if h1_number is not None:
        # The H1 is the identity (ADR-0089), so a declared number always
        # counts for something. It agrees with the filename or it does not --
        # and "the filename carries no number at all" is a DISAGREEMENT, not a
        # pass. That was MB-1: `notes.md` holding `# ADR-0091:` was accepted
        # into by_number, so renaming an ADR out of its own naming convention
        # hid it from every duplicate check.
        if filename_number == h1_number:
            return "counted", (h1_number, rel)
        return "mismatched", (rel, filename_number, h1_number)
    if filename_number is not None:
        # Looks like an ADR, declares nothing. The exemption-list bucket.
        return "unrecognised", rel
    if name_gestures or gestures_in_h1(first_line):
        # Gestures at a number but parses under neither strict pattern -- a
        # three-digit id, an en-dash separator, a lowercase `adr`, a Unicode
        # numeral, a BOM in front of the heading, a typo nobody has thought
        # of yet. Loud, never silent. This branch is the asymmetry doing its
        # job: it fires exactly when the over-broad detector and strict
        # identity DISAGREE.
        return "unrecognised", rel
    return "not_an_adr", rel


def scan_adr_directory(path):
    """Classify every file under an ADR tree. Nothing is ever silently dropped.

    Walks RECURSIVELY (MB-2: `os.listdir` is flat, so
    `docs/decisions/nested/ADR-0090-nested.md` evaded the duplicate check
    completely). Names are reported relative to `path`, so a nested collision
    is distinguishable from a top-level one; on today's flat tree a relative
    path is just the basename.

    Returns a dict with six keys:

      by_number    {'0073': [rel, rel], ...} -- files whose H1 declares a
                   number their filename agrees with. A key with more than one
                   entry is a duplicate id.
      mismatched   [(rel, filename_number, h1_number)] -- the H1 declares a
                   number and the filename does not agree. `filename_number`
                   is None when the filename carries no number at all.
      unrecognised [rel] -- gestures at an ADR number but declares none. Must
                   be covered by `NON_ADR_EXEMPTIONS`.
      not_an_adr   [rel] -- claims nothing anywhere. The only silent bucket,
                   and reachable only when both the filename and the H1 are
                   free of any ADR number.
      symlinked    [rel] -- symlinks (files and directories), recorded and NOT
                   followed.
      seen         [rel] -- every file walked, so the caller can prove the
                   buckets account for all of them.
    """
    buckets = {name: [] for name in BUCKETS}
    by_number = {}
    seen = []
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        # `os.walk` will not DESCEND into a symlinked directory with
        # followlinks=False, but it would still leave it silently unvisited.
        # Record and prune, so it is refused rather than ignored.
        for name in sorted(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                rel = os.path.relpath(os.path.join(dirpath, name), path)
                seen.append(rel)
                buckets["symlinked"].append(rel)
        dirnames[:] = [d for d in sorted(dirnames)
                       if not os.path.islink(os.path.join(dirpath, d))]
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), path)
            seen.append(rel)
            bucket, payload = _classify(path, rel)
            if bucket == "counted":
                number, entry = payload
                by_number.setdefault(number, []).append(entry)
                buckets["counted"].append(entry)
            else:
                buckets[bucket].append(payload)
    for value in by_number.values():
        value.sort()
    # The invariant this round exists to establish, checked rather than
    # assumed. `_classify` is total, so this cannot fire today -- it is here so
    # that an edit which adds a `continue`, or a bucket nobody wired up, fails
    # LOUDLY at the next test run instead of quietly shrinking the scan.
    accounted = [r for name in BUCKETS
                 for r in (buckets[name] if name != "mismatched"
                           else [m[0] for m in buckets[name]])]
    if sorted(accounted) != sorted(seen):
        raise AssertionError(
            "scan_adr_directory dropped files: "
            f"{sorted(set(seen) - set(accounted))}")
    return {"by_number": by_number,
            "mismatched": buckets["mismatched"],
            "unrecognised": buckets["unrecognised"],
            "not_an_adr": buckets["not_an_adr"],
            "symlinked": buckets["symlinked"],
            "seen": seen}


def duplicates(scan):
    return {n: f for n, f in scan["by_number"].items() if len(f) > 1}


class AdrSequenceAllocator(unittest.TestCase):
    """REFUSE: `seq next --name adr`, the mechanism ADR-0066 named as the real fix."""

    def test_name_is_free_form_and_adr_needs_no_code_path(self):
        """[CHARACTERIZATION] `--name adr` allocates with zero binary support.

        This is #619's central finding and the reason the task is size S:
        `cmd_seq_next` is name-agnostic, so `adr` is a caller convention. If
        this ever fails, somebody has added an `adr` branch, constant or
        `choices=` and thrown away the generality.
        """
        c = Ctl()
        self.assertEqual(c.run("seq", "next", "--name", "adr"), (0, {
            "ok": True, "name": "adr", "value": 1}))
        self.assertEqual(c.run("seq", "next", "--name", "adr"), (0, {
            "ok": True, "name": "adr", "value": 2}))

    def test_start_seeds_on_first_insert_only(self):
        """[CHARACTERIZATION] `--start 76` returns 76 then 77, and is ignored after.

        The whole bootstrap rests on this. `ON CONFLICT(name) DO NOTHING` means
        there is exactly one chance to create the row at 76; afterwards
        `--start` is silently ignored and recovery needs `seq set`. That is why
        ADR-0089 seeded before the PR rather than after it.
        """
        c = Ctl()
        self.assertEqual(c.run("seq", "next", "--name", "adr", "--start", "76"),
                         (0, {"ok": True, "name": "adr", "value": 76}))
        self.assertEqual(c.run("seq", "next", "--name", "adr"),
                         (0, {"ok": True, "name": "adr", "value": 77}))
        # Silently ignored, not honoured, not an error.
        self.assertEqual(c.run("seq", "next", "--name", "adr", "--start", "9999"),
                         (0, {"ok": True, "name": "adr", "value": 78}))

    def test_sixteen_concurrent_allocations_are_unique_and_gapless(self):
        """[CHARACTERIZATION] The atomicity ADR numbering is about to depend on.

        Same shape as `test_leases.test_sequence_atomic_and_lease_gated`, run
        against `adr` rather than `migrations`: sixteen genuinely separate
        processes, so `BEGIN IMMEDIATE` is exercised the way concurrent agents
        hit it. Two crews computing the next free number concurrently agreed
        with each other and were both wrong -- twice, 65 minutes apart, in
        production (`.omni/tech-debt.md:39`). This is the property that makes
        that unrepresentable.
        """
        c = Ctl()
        c.run("seq", "next", "--name", "adr", "--start", "76")
        cmds = [c.cmdline("seq", "next", "--name", "adr") for _ in range(16)]
        results = run_parallel(cmds)
        values = sorted(r["value"] for code, r in results if code == 0)
        self.assertEqual(values, list(range(77, 93)))

    def test_two_sequences_do_not_interfere(self):
        """[CHARACTERIZATION] `adr` and `migrations` are independent counters.

        Interleaved in ONE parallel batch, not merely alternated, so a shared
        row or a name-blind UPDATE would show up as a value appearing in the
        wrong sequence.
        """
        c = Ctl()
        c.run("seq", "next", "--name", "adr", "--start", "76")
        c.run("lease", "acquire", "--resource", MIG, "--holder", "crew_beta",
              "--mode", "serialize")
        batch = []
        for _ in range(8):
            batch.append(c.cmdline("seq", "next", "--name", "adr"))
            batch.append(c.cmdline("seq", "next", "--name", "migrations",
                                   "--resource", MIG, "--holder", "crew_beta"))
        results = run_parallel(batch)
        adr = sorted(r["value"] for code, r in results
                     if code == 0 and r["name"] == "adr")
        mig = sorted(r["value"] for code, r in results
                     if code == 0 and r["name"] == "migrations")
        self.assertEqual(adr, list(range(77, 85)))
        self.assertEqual(mig, list(range(1, 9)))

    def test_lease_gate_is_optional_and_unchanged(self):
        """[CHARACTERIZATION] The gate `require_sequence_lease` was lifted out of.

        Pins the deny path byte for byte -- exit 3, the exact reason string,
        and `holder` reporting the actual holder rather than the requested one
        -- because this change moved that code into a shared helper and a
        refactor that alters a live control-plane refusal is a refactor that
        went wrong.
        """
        c = Ctl()
        code, body = c.run("seq", "next", "--name", "migrations",
                           "--resource", MIG, "--holder", "crew_beta")
        self.assertEqual(code, 3)
        self.assertEqual(body["reason"],
                         "sequence allocation requires holding the lease on "
                         + MIG)
        self.assertIsNone(body["holder"])
        c.run("lease", "acquire", "--resource", MIG, "--holder", "crew_beta",
              "--mode", "serialize")
        self.assertEqual(c.run("seq", "next", "--name", "migrations",
                               "--resource", MIG, "--holder", "crew_beta"),
                         (0, {"ok": True, "name": "migrations", "value": 1}))
        code, body = c.run("seq", "next", "--name", "migrations",
                           "--resource", MIG, "--holder", "crew_alpha")
        self.assertEqual(code, 3)
        self.assertEqual(body["holder"], "crew_beta")

    def test_a_denied_allocation_does_not_burn_a_number(self):
        """[CHARACTERIZATION] The deny path COMMITs without having incremented.

        `out()` is `sys.exit`, so the refusal COMMITs an open `BEGIN
        IMMEDIATE` before exiting. If that COMMIT ever landed AFTER an
        increment, every refused call would silently consume an ADR number and
        the sequence would grow gaps that look exactly like abandoned drafts.
        """
        c = Ctl()
        c.run("seq", "next", "--name", "adr", "--start", "76")
        for _ in range(3):
            code, _ = c.run("seq", "next", "--name", "adr",
                            "--resource", MIG, "--holder", "crew_beta")
            self.assertEqual(code, 3)
        self.assertEqual(c.run("seq", "next", "--name", "adr"),
                         (0, {"ok": True, "name": "adr", "value": 77}))


class SeqSetIsNoLongerAnUngatedOverride(unittest.TestCase):
    """RED-ON-BASE: the hand-override that made the allocator's guarantee false."""

    def test_rewinding_an_existing_counter_is_refused(self):
        """[RED-ON-BASE] Base returns ok:true and reissues the number.

        Watch it fail: against the base binary this returns exit 0 and
        `{"ok": true, "next": 1}`, and the following `seq next` hands back 76 a
        second time.
        """
        c = Ctl()
        c.run("seq", "next", "--name", "adr", "--start", "76")
        code, body = c.run("seq", "set", "--name", "adr", "--next", "1")
        self.assertEqual(code, 3)
        self.assertFalse(body["ok"])
        self.assertIn("refusing to rewind sequence adr", body["reason"])
        self.assertIn("--allow-rewind", body["reason"])
        self.assertEqual(body["next"], 77)
        self.assertEqual(body["requested"], 1)
        # The refusal COMMITs before exiting, and the counter is untouched.
        self.assertEqual(c.run("seq", "next", "--name", "adr"),
                         (0, {"ok": True, "name": "adr", "value": 77}))

    def test_allow_rewind_keeps_the_escape_hatch_open(self):
        """[RED-ON-BASE] Base has no such flag; argparse rejects it (exit 2).

        A poisoned counter has no other repair route, so the hatch must stay.
        The change makes rewinding deliberate, not impossible.
        """
        c = Ctl()
        c.run("seq", "next", "--name", "adr", "--start", "76")
        self.assertEqual(
            c.run("seq", "set", "--name", "adr", "--next", "5",
                  "--allow-rewind"),
            (0, {"ok": True, "name": "adr", "next": 5}))
        self.assertEqual(c.run("seq", "next", "--name", "adr"),
                         (0, {"ok": True, "name": "adr", "value": 5}))

    def test_creating_a_new_sequence_at_any_value_stays_unconditional(self):
        """[CHARACTERIZATION] The documented restore path must not regress.

        `deploy/nightshift-state-backup.sh:29-30` restores a rebuilt box by
        re-seeding sequences with `seq set`. A fresh plane has no row, so
        restore CREATES rather than lowers and never needs `--allow-rewind`.
        Breaking this would trade a numbering bug for an unrecoverable box.
        """
        c = Ctl()
        self.assertEqual(c.run("seq", "set", "--name", "adr", "--next", "76"),
                         (0, {"ok": True, "name": "adr", "next": 76}))
        self.assertEqual(c.run("seq", "next", "--name", "adr"),
                         (0, {"ok": True, "name": "adr", "value": 76}))

    def test_raising_a_counter_stays_unconditional(self):
        """[CHARACTERIZATION] Only LOWERING is dangerous; raising is monotone."""
        c = Ctl()
        c.run("seq", "next", "--name", "adr", "--start", "76")
        self.assertEqual(c.run("seq", "set", "--name", "adr", "--next", "500"),
                         (0, {"ok": True, "name": "adr", "next": 500}))

    def test_setting_the_same_value_is_a_no_op_not_a_rewind(self):
        """[CHARACTERIZATION] An idempotent restore must not need the flag."""
        c = Ctl()
        c.run("seq", "set", "--name", "adr", "--next", "76")
        self.assertEqual(c.run("seq", "set", "--name", "adr", "--next", "76"),
                         (0, {"ok": True, "name": "adr", "next": 76}))

    def test_seq_set_honours_the_same_lease_gate_as_seq_next(self):
        """[RED-ON-BASE] Base's `seq set` has no `--resource` flag at all.

        The gate is the shared helper, so the refusal shape matches `seq
        next`'s exactly apart from the verb -- "sequence set" rather than
        "sequence allocation", because the old wording is simply false here.
        """
        c = Ctl()
        code, body = c.run("seq", "set", "--name", "migrations", "--next", "9",
                           "--resource", MIG, "--holder", "crew_beta")
        self.assertEqual(code, 3)
        self.assertEqual(body["reason"],
                         "sequence set requires holding the lease on " + MIG)
        self.assertIsNone(body["holder"])
        c.run("lease", "acquire", "--resource", MIG, "--holder", "crew_beta",
              "--mode", "serialize")
        self.assertEqual(
            c.run("seq", "set", "--name", "migrations", "--next", "9",
                  "--resource", MIG, "--holder", "crew_beta"),
            (0, {"ok": True, "name": "migrations", "next": 9}))
        code, _ = c.run("seq", "set", "--name", "migrations", "--next", "9",
                        "--resource", MIG, "--holder", "crew_alpha")
        self.assertEqual(code, 3)

    def test_a_refused_set_leaves_no_open_transaction(self):
        """[RED-ON-BASE] The new refusal path must COMMIT before `out()`.

        `out()` is `sys.exit`; an open `BEGIN IMMEDIATE` rolls back on close
        AND holds the write lock until the process dies. A later command
        succeeding immediately is the observable proof the lock was released.
        """
        c = Ctl()
        c.run("seq", "next", "--name", "adr", "--start", "76")
        code, _ = c.run("seq", "set", "--name", "adr", "--next", "1")
        self.assertEqual(code, 3)
        self.assertEqual(c.run("seq", "set", "--name", "adr", "--next", "900"),
                         (0, {"ok": True, "name": "adr", "next": 900}))


class DumpExposesSequences(unittest.TestCase):
    """RED-ON-BASE: the counter must be observable and backed up."""

    def test_dump_emits_the_sequences_table(self):
        """[RED-ON-BASE] Base's `dump` has no `sequences` key.

        `deploy/nightshift-state-backup.sh:29-30` documents sequences as
        riding this snapshot so a restored box can re-seed from it, and they
        were not in it -- the restore path had nothing to read. Harmless while
        sequences only numbered migrations; not harmless once ADR numbers
        depend on the counter, because a restore then resets it and the next
        bare `seq next` returns 1, colliding with an ADR-0001 that already has
        two files on main.
        """
        c = Ctl()
        c.run("seq", "next", "--name", "adr", "--start", "76")
        c.run("lease", "acquire", "--resource", MIG, "--holder", "crew_beta",
              "--mode", "serialize")
        c.run("seq", "next", "--name", "migrations", "--resource", MIG,
              "--holder", "crew_beta")
        code, body = c.run("dump")
        self.assertEqual(code, 0)
        self.assertIn("sequences", body)
        self.assertEqual(body["sequences"],
                         [{"name": "adr", "next": 77},
                          {"name": "migrations", "next": 2}])

    def test_dumped_sequences_round_trip_through_seq_set(self):
        """[RED-ON-BASE] The restore path, end to end, on a fresh plane.

        Reads the counter out of one plane's `dump` and re-seeds a second,
        empty plane from it -- which is exactly what a rebuilt box does. Both
        halves are needed: `dump` must emit the value, and `seq set` must
        still be allowed to create from it without `--allow-rewind`.
        """
        source = Ctl()
        source.run("seq", "next", "--name", "adr", "--start", "76")
        _, body = source.run("dump")
        restored = Ctl()
        for row in body["sequences"]:
            self.assertEqual(
                restored.run("seq", "set", "--name", row["name"],
                             "--next", str(row["next"])),
                (0, {"ok": True, "name": row["name"], "next": row["next"]}))
        self.assertEqual(restored.run("seq", "next", "--name", "adr"),
                         (0, {"ok": True, "name": "adr", "value": 77}))


class AdrIdUniquenessInThisRepository(unittest.TestCase):
    """NEW-GUARD: DETECT. On a `pull_request` event this scans the merge commit."""

    def test_no_adr_number_has_two_documents(self):
        """[NEW-GUARD] The check whose absence let #594 merge clean.

        `git merge-tree --write-tree origin/main <head>` produced two
        `ADR-0069-*` blobs -- different filenames, same key, exit 0, no
        conflict. Nothing in the pipeline caught it; a human found it during a
        review gate that had been dispatched for an unrelated reason.

        Green on base by construction (it asserts a property of the repository,
        not of the binary). `CheckerFailsWhenTheRepositoryIsWrong` below is the
        WA-008 control that proves it can go red.
        """
        found = duplicates(scan_adr_directory(DECISIONS))
        self.assertEqual(found, {}, f"duplicate ADR ids in {DECISIONS}: {found}")

    def test_every_filename_agrees_with_its_h1(self):
        """[NEW-GUARD] A disagreement is a half-renamed ADR, i.e. a live collision.

        The remedy for a collision is `git mv`. A rename that moves the file
        but not the H1 leaves the document claiming a number it does not have,
        which is how a "fixed" collision comes back.
        """
        scan = scan_adr_directory(DECISIONS)
        self.assertEqual(scan["mismatched"], [])

    def test_adr_shaped_filenames_that_are_not_adrs_are_named_explicitly(self):
        """[NEW-GUARD] Unrecognised H1 shapes fail; they are never skipped.

        This is the trap ADR-0089 asked to be pinned. A file that quietly drops
        out of the scan is worse than one that fires falsely -- it is a place a
        real duplicate can hide. Today there is exactly one such file, and it
        is not an ADR. If this fails for a NEW file, the answer is almost
        always to fix that file's H1, not to extend the exemption list.
        """
        scan = scan_adr_directory(DECISIONS)
        self.assertEqual(sorted(scan["unrecognised"]),
                         sorted(NON_ADR_EXEMPTIONS))

    def test_the_exemption_list_stays_small(self):
        """[NEW-GUARD] ADR-0089 reversal trigger 3, made mechanical.

        Past roughly three entries the filename convention is the defect, not
        the checker, and this ADR says to revisit rather than keep appending.
        """
        self.assertLessEqual(len(NON_ADR_EXEMPTIONS), 3)

    def test_the_real_tree_contains_no_symlinks(self):
        """MB-4's OTHER half: refused in the repository, not merely unfollowed.

        The scan stopping at a symlink is read-safety, and fixtures cover it.
        This is the assertion that makes ADR-0089's word "refused" true rather
        than aspirational: a symlink checked into `docs/decisions/` FAILS CI
        instead of being quietly recorded while everything stays green.

        Found while re-reading the ADR's third amendment. It said symlinks are
        "recorded and refused"; the shipped behaviour was recorded and
        TOLERATED, because `symlinked` was asserted only in synthetic
        fixtures. Prose outrunning code by one word is the defect this whole
        task keeps producing, and ADR-0089 is the document that names it.
        """
        self.assertEqual(scan_adr_directory(DECISIONS)["symlinked"], [])

    def test_the_partition_holds_over_the_real_tree(self):
        """The contract, asserted where it protects something real.

        `test_every_file_seen_lands_in_exactly_one_bucket` proves this on
        synthetic fixtures. Asserting it ONLY there would repeat, one level
        up, exactly the omission the symlink guard above fixes: a property
        pinned in the laboratory and never on the patient.

        DELIBERATELY NOT ASSERTED HERE: the CONTENTS of `not_an_adr` and
        `seen`. Ordinary prose in `docs/decisions/` is legitimate and
        `ADR-TEMPLATE.md` lives there, so pinning those lists would generate
        churn on every added file while protecting nothing. That omission is a
        judgement call, not the same oversight twice -- please do not "fix" it
        by adding an expected-file list.
        """
        scan = scan_adr_directory(DECISIONS)
        members = ([f for v in scan["by_number"].values() for f in v]
                   + [m[0] for m in scan["mismatched"]]
                   + scan["unrecognised"] + scan["not_an_adr"]
                   + scan["symlinked"])
        self.assertEqual(sorted(members), sorted(scan["seen"]),
                         "buckets do not partition docs/decisions/")
        self.assertEqual(len(members), len(set(members)),
                         "a real file was placed in more than one bucket")

    def test_the_real_staff_review_file_is_still_the_shape_we_exempted(self):
        """[NEW-GUARD] The exemption is justified by the file, not by its name.

        Guards against the exemption outliving its reason: if this filename is
        ever reused for a genuine ADR, the entry would silently excuse a real
        duplicate. Skips rather than fails if the file is gone, because the
        right response to its removal is deleting the exemption, not breaking
        the suite for whoever removed it.
        """
        path = os.path.join(DECISIONS, "ADR-0001-0003-staff-review.md")
        if not os.path.exists(path):
            self.skipTest("exempted file no longer present; drop the exemption")
        with open(path, encoding="utf-8") as handle:
            self.assertIsNone(ADR_H1.match(handle.readline().rstrip("\n")))


class CheckerFailsWhenTheRepositoryIsWrong(unittest.TestCase):
    """WA-008 control for the NEW-GUARD tests: what they report when wrong.

    Synthetic fixtures, so this stays true forever and offline regardless of
    what `docs/decisions/` happens to contain.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="adr-scan-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, first_line):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(first_line + "\n\nbody\n")

    def test_two_documents_on_one_number_are_reported(self):
        """The #594 collision, reproduced: different filenames, same key."""
        self.write("ADR-0073-f1-disposition-reads-integrated.md",
                   "# ADR-0073: F-1's disposition states the integrated truth")
        self.write("ADR-0073-rt17-current-generation.md",
                   "# ADR-0073: RT-17 current generation and fallible F-1 evidence")
        self.assertEqual(
            duplicates(scan_adr_directory(self.dir)),
            {"0073": ["ADR-0073-f1-disposition-reads-integrated.md",
                      "ADR-0073-rt17-current-generation.md"]})

    def test_four_documents_on_one_number_are_reported(self):
        """0069 really did reach four documents across main, drafts and salvage."""
        for slug in ("a", "b", "c", "d"):
            self.write(f"ADR-0069-{slug}.md", f"# ADR-0069: variant {slug}")
        self.assertEqual(len(duplicates(scan_adr_directory(self.dir))["0069"]), 4)

    def test_both_h1_separator_forms_are_recognised(self):
        """The em-dash form is 8 real ADRs; dropping it would hole the scan.

        Deliberately makes the two forms COLLIDE on one number, so a checker
        that recognised only the colon form would report no duplicate here and
        this test would fail -- which is the point.
        """
        self.write("ADR-0013-colon.md", "# ADR-0013: colon form")
        self.write("ADR-0013-emdash.md", "# ADR-0013 — em dash form")
        self.assertEqual(len(duplicates(scan_adr_directory(self.dir))["0013"]), 2)

    def test_a_staff_review_filename_is_not_mistaken_for_a_duplicate(self):
        """The real `main` false positive a filename-only rule would produce."""
        self.write("ADR-0001-stack-and-shape.md", "# ADR-0001: Stack and shape")
        self.write("ADR-0001-0003-staff-review.md",
                   "# Staff review: ADR-0001, ADR-0002, ADR-0003")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(duplicates(scan), {})
        self.assertEqual(scan["unrecognised"], ["ADR-0001-0003-staff-review.md"])

    def test_a_filename_h1_disagreement_is_reported_not_counted(self):
        """A half-applied rename: filename says 0074, the document says 0073.

        Reported as `mismatched`, and deliberately NOT filed under 0073 in
        `by_number` -- counting it there would let a botched rename mask the
        very collision it was supposed to resolve.
        """
        self.write("ADR-0074-renamed.md", "# ADR-0073: not renamed inside")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["mismatched"],
                         [("ADR-0074-renamed.md", "0074", "0073")])
        self.assertEqual(scan["by_number"], {})

    def test_an_en_dash_separator_is_not_accepted(self):
        """The canonical near-miss the fail-don't-skip rule exists to catch.

        EN DASH (U+2013) is visually almost identical to the EM DASH (U+2014)
        and appears in no ADR on `main`. ADR-0089 names the separator set as
        exactly `:` and EM DASH and says the en dash must FAIL. A checker
        broader than its own spec would accept it silently -- this ADR's
        failure mode pointed at its own mechanism -- so the file must land in
        `unrecognised`, never in `by_number`.
        """
        self.write("ADR-0090-en-dash.md", "# ADR-0090 – en dash form")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {})
        self.assertEqual(scan["unrecognised"], ["ADR-0090-en-dash.md"])

    def test_an_em_dash_is_still_accepted_alongside_it(self):
        """The control for the test above: the two dashes are told apart.

        Without this, tightening the class to reject BOTH dashes would pass
        the en-dash test while silently dropping the eight real em-dash ADRs
        back out of the scan -- the exact hole the ADR was amended to close.
        """
        self.write("ADR-0090-em-dash.md", "# ADR-0090 — em dash form")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {"0090": ["ADR-0090-em-dash.md"]})
        self.assertEqual(scan["unrecognised"], [])

    def test_a_bare_adr_filename_without_a_slug_is_still_a_candidate(self):
        """`ADR-0090.md` must FAIL, not escape by not looking like a candidate.

        ADR-0089's candidate-file rule makes the slug OPTIONAL for exactly
        this reason. A slug-requiring pattern lets a file with no declaring H1
        drop out of the scan entirely, which is a hole that only a future
        filename would ever expose -- and nobody would remember to check it.
        """
        self.write("ADR-0090.md", "# Notes on numbering, not an ADR")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {})
        self.assertEqual(scan["unrecognised"], ["ADR-0090.md"])

    def test_a_bare_adr_filename_that_does_declare_is_counted(self):
        """The control: an optional slug must not stop a real ADR being seen.

        Pairs with the bare filename above -- one that declares is counted
        under its number, one that does not is failed. A pattern change that
        broke either half would show up here.
        """
        self.write("ADR-0090.md", "# ADR-0090: no slug but a real declaration")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {"0090": ["ADR-0090.md"]})
        self.assertEqual(scan["unrecognised"], [])

    def test_a_template_claims_nothing_and_lands_in_the_silent_bucket(self):
        """`ADR-TEMPLATE.md` is the one shape allowed to be skipped quietly.

        Neither its filename nor its H1 carries a digit where a number would
        go, so it gestures at no id at all. `not_an_adr` is the ONLY silent
        bucket and this is what it is for -- it is still ACCOUNTED FOR, which
        is the difference between this and the four defects fixed this round.
        """
        self.write("ADR-TEMPLATE.md", "# ADR-NNNN: <the decision in one line>")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {})
        self.assertEqual(scan["mismatched"], [])
        self.assertEqual(scan["unrecognised"], [])
        self.assertEqual(scan["not_an_adr"], ["ADR-TEMPLATE.md"])
        self.assertEqual(scan["seen"], ["ADR-TEMPLATE.md"])

    def test_a_clean_directory_reports_nothing(self):
        """The control's control: with the property TRUE, all three lists are empty."""
        self.write("ADR-0075-a.md", "# ADR-0075: fine")
        self.write("ADR-0089-b.md", "# ADR-0089 — also fine")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(duplicates(scan), {})
        self.assertEqual(scan["mismatched"], [])
        self.assertEqual(scan["unrecognised"], [])


class NothingIsEverSilentlyDropped(unittest.TestCase):
    """#619 round-1 review: four MERGE_BLOCKERs that were all one defect.

    `by_number` gained entries, `mismatched` and `unrecognised` stayed EMPTY,
    and files vanished. The reviewer's own probe, verbatim:

        by_number   : {'0001': [...], '0090': ['ADR-0090-link.md'],
                       '0091': ['notes.md']}
        mismatched  : []
        unrecognised: []

    Five files on disk, three accounted for. `test_every_file_seen_lands_in
    _exactly_one_bucket` below is the one that matters: it is the assertion
    the four individual tests are instances of, and it is what makes a FIFTH
    unanticipated shape fail by construction instead of needing a fifth
    finding.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="adr-scan-drop-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, rel, first_line):
        full = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(first_line + "\n\nbody\n")
        return full

    def buckets_of(self, rel):
        scan = scan_adr_directory(self.dir)
        return [name for name, members in (
            ("counted", [f for v in scan["by_number"].values() for f in v]),
            ("mismatched", [m[0] for m in scan["mismatched"]]),
            ("unrecognised", scan["unrecognised"]),
            ("not_an_adr", scan["not_an_adr"]),
            ("symlinked", scan["symlinked"]),
        ) if rel in members]

    # ---------------------------------------------------- the through-line --

    def test_every_file_seen_lands_in_exactly_one_bucket(self):
        """The invariant the four findings below are instances of.

        Builds one directory holding every shape this file knows about --
        good, duplicate, nested, five-digit, declaring-under-a-non-ADR-name,
        exempt, template, malformed id, en dash, non-markdown, symlink -- and
        asserts the buckets partition `seen` exactly. Not "covers": PARTITIONS.
        A file counted twice fails this as surely as one dropped.
        """
        self.write("ADR-0001-good.md", "# ADR-0001: good")
        self.write("ADR-0002-a.md", "# ADR-0002: dupe a")
        self.write("ADR-0002-b.md", "# ADR-0002 \u2014 dupe b")
        self.write("nested/ADR-0003-nested.md", "# ADR-0003: nested")
        self.write("ADR-10000-five.md", "# ADR-10000: five digits")
        self.write("notes.md", "# ADR-0091: declared under a non-ADR name")
        self.write("ADR-0004-no-declaration.md", "Not a heading at all")
        self.write("ADR-TEMPLATE.md", "# ADR-NNNN: template")
        self.write("ADR-090-three-digits.md", "# ADR-090: malformed id")
        self.write("ADR-0005-en-dash.md", "# ADR-0005 \u2013 en dash")
        self.write("README.txt", "not markdown")
        os.symlink(self.write("outside.md", "# ADR-0006: target"),
                   os.path.join(self.dir, "ADR-0006-link.md"))

        scan = scan_adr_directory(self.dir)
        members = ([f for v in scan["by_number"].values() for f in v]
                   + [m[0] for m in scan["mismatched"]]
                   + scan["unrecognised"] + scan["not_an_adr"]
                   + scan["symlinked"])
        self.assertEqual(sorted(members), sorted(scan["seen"]),
                         "buckets do not partition the files seen")
        self.assertEqual(len(members), len(set(members)),
                         "a file was placed in more than one bucket")
        self.assertEqual(len(scan["seen"]), 13)
        # And the silent bucket really is the only quiet one: everything in it
        # gestures at no ADR number anywhere. `outside.md` is deliberately NOT
        # here -- it declares `# ADR-0006:` under a filename carrying no
        # number, which is MB-1's shape, so it belongs in `mismatched`. My
        # first draft of this assertion expected it in `not_an_adr` and the
        # test caught me, which is the invariant doing its job on its author.
        self.assertEqual(sorted(scan["not_an_adr"]),
                         ["ADR-TEMPLATE.md", "README.txt"])
        self.assertIn("outside.md", [m[0] for m in scan["mismatched"]])

    def test_the_reviewers_probe_leaves_nothing_unaccounted(self):
        """The exact five-file probe from the review, end to end."""
        self.write("ADR-0001-good.md", "# ADR-0001: good")
        self.write("notes.md", "# ADR-0091: hidden occupant")
        self.write("nested/ADR-0090-nested.md", "# ADR-0090: nested")
        self.write("ADR-10000-five.md", "# ADR-10000: five")
        os.symlink(self.write("outside.md", "# ADR-0090: outside"),
                   os.path.join(self.dir, "ADR-0090-link.md"))
        scan = scan_adr_directory(self.dir)
        self.assertNotEqual(scan["mismatched"], [],
                            "mismatched was empty in the review probe")
        self.assertEqual(scan["by_number"]["0090"],
                         ["nested/ADR-0090-nested.md"])
        self.assertEqual(scan["by_number"]["10000"], ["ADR-10000-five.md"])
        self.assertEqual(scan["symlinked"], ["ADR-0090-link.md"])

    # -------------------------------------------------- the four findings --

    def test_mb1_a_declaring_h1_under_a_non_adr_filename_is_a_mismatch(self):
        """MB-1: renaming an ADR out of its convention hid it from every check.

        `notes.md` carrying `# ADR-0091:` was accepted straight into
        `by_number` because filename/H1 agreement was only enforced when the
        filename was already ADR-shaped. ADR-0089 says the number comes from
        the H1 and the filename must agree -- a filename carrying NO number is
        a disagreement, not an exemption from the rule.
        """
        self.write("notes.md", "# ADR-0091: hidden occupant")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {})
        self.assertEqual(scan["mismatched"], [("notes.md", None, "0091")])
        self.assertEqual(self.buckets_of("notes.md"), ["mismatched"])

    def test_mb2_a_nested_duplicate_is_found(self):
        """MB-2: `os.listdir` is flat, so a subdirectory evaded CI entirely.

        Asserts the DUPLICATE, not merely the visibility: a nested file that
        is seen but not compared against its top-level twin would satisfy a
        weaker test while leaving the collision unreported.
        """
        self.write("ADR-0090-top.md", "# ADR-0090: top level")
        self.write("nested/ADR-0090-nested.md", "# ADR-0090: nested")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(duplicates(scan),
                         {"0090": ["ADR-0090-top.md",
                                   "nested/ADR-0090-nested.md"]})

    def test_mb3_identifiers_above_9999_are_still_checked(self):
        """MB-3: DETECT switched itself off at five digits.

        `(\\d{4})` matched neither pattern for `ADR-10000-*`, so the file was
        not even a candidate. The counter is monotone and unbounded above; a
        check that stops at a round number is this ADR's failure mode aimed at
        its own mechanism, on a timer.
        """
        self.write("ADR-10000-a.md", "# ADR-10000: five digits a")
        self.write("ADR-10000-b.md", "# ADR-10000: five digits b")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(duplicates(scan),
                         {"10000": ["ADR-10000-a.md", "ADR-10000-b.md"]})

    def test_mb3_four_digit_ids_are_unaffected_by_the_widening(self):
        """Control for MB-3: `\\d{4,}` must not change today's tree.

        Without this, a pattern that accidentally required five or more digits
        would satisfy the test above while dropping all 62 real ADRs.
        """
        self.write("ADR-0090-a.md", "# ADR-0090: four digits")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {"0090": ["ADR-0090-a.md"]})

    def test_mb4_a_symlink_is_recorded_and_never_followed(self):
        """MB-4: a checked-in symlink made DETECT read outside the directory.

        The target here declares a DIFFERENT number from the link name and
        lives outside the scanned tree. Following it accepted that content as
        an ADR; the fix records the link and reads nothing.
        """
        outside = os.path.join(self.dir, "..",
                               os.path.basename(self.dir) + "-target.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("# ADR-0090: target outside the scanned tree\n")
        self.addCleanup(os.remove, outside)
        os.symlink(outside, os.path.join(self.dir, "ADR-0090-link.md"))
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {})
        self.assertEqual(scan["symlinked"], ["ADR-0090-link.md"])
        self.assertEqual(self.buckets_of("ADR-0090-link.md"), ["symlinked"])

    def test_mb4_a_symlinked_directory_is_recorded_and_not_descended(self):
        """`os.walk(followlinks=False)` would leave it silently unvisited.

        Not descending is correct; leaving no trace is the same silent-skip
        defect one level up. It must be recorded.
        """
        real = os.path.join(self.dir, "real")
        os.makedirs(real)
        with open(os.path.join(real, "ADR-0090-a.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("# ADR-0090: inside the real directory\n")
        os.symlink(real, os.path.join(self.dir, "linkdir"))
        scan = scan_adr_directory(self.dir)
        self.assertIn("linkdir", scan["symlinked"])
        self.assertEqual(scan["by_number"], {"0090": ["real/ADR-0090-a.md"]})

    # ------------------------------------------- the residue, made not-silent --

    def test_a_malformed_identifier_is_loud_rather_than_ignored(self):
        """Three digits parses under neither pattern -- and must still be seen.

        This is the fifth shape nobody filed. Before the restructure it would
        have been dropped exactly as MB-3 was, and it is caught here because
        the function is total, not because anyone anticipated it.
        """
        self.write("ADR-090-three-digits.md", "# ADR-090: malformed")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["unrecognised"], ["ADR-090-three-digits.md"])

    def test_an_en_dash_h1_under_a_non_adr_filename_is_loud(self):
        """Neither side parses strictly, but the H1 gestures at a number."""
        self.write("notes.md", "# ADR-0090 \u2013 en dash, non-ADR filename")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["by_number"], {})
        self.assertEqual(scan["unrecognised"], ["notes.md"])

    def test_an_adr_shaped_non_markdown_file_is_loud_and_never_opened(self):
        """A binary dropped here must not break the scan, nor vanish from it."""
        with open(os.path.join(self.dir, "ADR-0090-data.bin"), "wb") as handle:
            handle.write(b"\x00\xff\xfe not text at all")
        scan = scan_adr_directory(self.dir)
        self.assertEqual(scan["unrecognised"], ["ADR-0090-data.bin"])


class TheGestureDetectorIsStrictlyBroaderThanIdentity(unittest.TestCase):
    """#619 round-2 review: three MERGE_BLOCKERs, one asymmetry.

    Round 1 produced the partition contract -- nothing is silently dropped.
    Round 2 showed the partition can hold while the buckets are WRONG: three
    shapes gestured at an ADR, failed identity, and landed in the quiet
    bucket anyway, because the gesture detector was not strictly broader than
    identity.

        lowercase      `adr-0090-x.md` + `# adr-0090:`  -> not_an_adr
        Unicode digits `ADR-\u0660\u0660\u0669\u0660-unicode.md`  -> counted as a DISTINCT
                       number; `duplicates()` was `{}`
        leading BOM    `\ufeff# ADR-0093:`               -> not_an_adr

    The fix is not three cases. It is the invariant below, tested directly:
    identity is a SUBSET of gesture, so the two can only ever disagree in the
    loud direction. `test_no_mutation_of_a_valid_adr_is_ever_quiet` is the
    one that makes a shape none of us has thought of loud by construction.
    """

    VALID_NAME = "ADR-0090-slug.md"
    VALID_H1 = "# ADR-0090: a perfectly ordinary decision record"

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="adr-gesture-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def bucket(self, name, first_line):
        for stale in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, stale))
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(first_line + "\n\nbody\n")
        scan = scan_adr_directory(self.dir)
        for number, files in scan["by_number"].items():
            if name in files:
                return "counted"
        for entry in scan["mismatched"]:
            if entry[0] == name:
                return "mismatched"
        if name in scan["unrecognised"]:
            return "unrecognised"
        if name in scan["not_an_adr"]:
            return "not_an_adr"
        return "MISSING"

    # ------------------------------------------------- the invariant itself --

    def test_identity_is_a_subset_of_gesture(self):
        """Anything strict identity accepts, the broad detector must also see.

        Asserted directly rather than inferred. If this ever fails, the two
        have crossed and a valid ADR can be classified by a detector that
        cannot see it -- which is the failure the whole asymmetry prevents.
        """
        for number in ("0001", "0090", "9999", "10000", "123456"):
            for separator in (":", "\u2014"):
                h1 = f"# ADR-{number}{separator} title"
                self.assertIsNotNone(ADR_H1.match(h1), h1)
                self.assertTrue(gestures_in_h1(h1), h1)
            for name in (f"ADR-{number}-slug.md", f"ADR-{number}.md"):
                self.assertIsNotNone(ADR_FILENAME.match(name), name)
                self.assertTrue(gestures_in_filename(name), name)

    def test_no_mutation_of_a_valid_adr_is_ever_quiet(self):
        """The headline. Mutate a valid ADR any of these ways: never silent.

        Each mutation either keeps identity (still `counted`) or breaks it
        (`unrecognised` / `mismatched`). What none of them may do is reach
        `not_an_adr`. The list is not the point -- the property is; the list
        just demonstrates it on the shapes two review rounds actually found,
        plus several nobody has reported.
        """
        mutations = {
            "lowercase name+h1": (self.VALID_NAME.lower(), self.VALID_H1.lower()),
            "lowercase h1 only": (self.VALID_NAME, self.VALID_H1.lower()),
            "leading BOM": (self.VALID_NAME, "\ufeff" + self.VALID_H1),
            "BOM, non-ADR name": ("notes.md", "\ufeff" + self.VALID_H1),
            "zero-width in ADR": (self.VALID_NAME,
                                  self.VALID_H1.replace("ADR", "A\u200bDR")),
            "zero-width in name": (self.VALID_NAME.replace("ADR", "A\u200bDR"),
                                   self.VALID_H1),
            "fullwidth letters": (self.VALID_NAME.replace("ADR", "\uff21\uff24\uff32"),
                                  self.VALID_H1.replace("ADR", "\uff21\uff24\uff32")),
            "arabic-indic digits": (
                self.VALID_NAME.replace("0090", "\u0660\u0660\u0669\u0660"),
                self.VALID_H1.replace("0090", "\u0660\u0660\u0669\u0660")),
            "fullwidth digits": (
                self.VALID_NAME.replace("0090", "\uff10\uff10\uff19\uff10"),
                self.VALID_H1.replace("0090", "\uff10\uff10\uff19\uff10")),
            "leading space in h1": (self.VALID_NAME, " " + self.VALID_H1),
            "nbsp after hash": (self.VALID_NAME,
                                self.VALID_H1.replace("# ", "#\u00a0")),
            "soft hyphen in ADR": (self.VALID_NAME,
                                   self.VALID_H1.replace("ADR", "AD\u00adR")),
        }
        quiet = {}
        for label, (name, h1) in mutations.items():
            got = self.bucket(name, h1)
            if got == "not_an_adr":
                quiet[label] = (name, h1)
        self.assertEqual(quiet, {},
                         "these mutations of a valid ADR were classified quiet")

    def test_the_control_an_untouched_valid_adr_is_still_counted(self):
        """Without this, refusing EVERYTHING would satisfy the test above."""
        self.assertEqual(self.bucket(self.VALID_NAME, self.VALID_H1), "counted")

    def test_the_control_a_template_is_still_quiet(self):
        """And the over-broad detector must not sweep in the one legitimate
        quiet file. `ADR-TEMPLATE.md` gestures at no number: `adr-` is
        followed by `t`, not a digit, in both its name and its heading."""
        self.assertEqual(
            self.bucket("ADR-TEMPLATE.md", "# ADR-NNNN: <the decision>"),
            "not_an_adr")

    # -------------------------------------------------- the three findings --

    def test_mb_lowercase_adr_is_loud(self):
        """Round 2: `adr-0090-x.md` + `# adr-0090:` was `not_an_adr`."""
        self.assertEqual(self.bucket("adr-0090-x.md", "# adr-0090: title"),
                         "unrecognised")

    def test_mb_unicode_digits_are_refused_not_counted_as_another_number(self):
        """Round 2: the sharpest of the three.

        `\\d` is Unicode-aware, so `ADR-\u0660\u0660\u0669\u0660` was read as the number
        "\u0660\u0660\u0669\u0660" -- not equal to "0090" -- and the two files sat in
        different `by_number` keys, so `duplicates()` was empty. A duplicate
        that does not register as one is #651's shape inside the mechanism
        built to end it. Identity now spells its digits `[0-9]`.
        """
        arabic = "\u0660\u0660\u0669\u0660"
        self.assertIsNone(ADR_FILENAME.match(f"ADR-{arabic}-x.md"))
        self.assertTrue(gestures_in_filename(f"ADR-{arabic}-x.md"))
        self.assertEqual(
            self.bucket(f"ADR-{arabic}-x.md", f"# ADR-{arabic}: title"),
            "unrecognised")

    def test_mb_a_bom_is_loud_under_both_filename_shapes(self):
        """Round 2: the two gates saw different results and BOTH were real.

        They tested different filename shapes. With an ADR-shaped name the
        filename still carried identity, so the file was already
        `unrecognised`; with a non-ADR name nothing carried identity and it
        fell quiet. Only the second was a defect, and pinning both is what
        stops a later change fixing one and regressing the other.
        """
        bom = "\ufeff"
        self.assertEqual(self.bucket("ADR-0093-x.md", bom + "# ADR-0093: t"),
                         "unrecognised")
        self.assertEqual(self.bucket("notes.md", bom + "# ADR-0093: t"),
                         "unrecognised")


if __name__ == "__main__":
    unittest.main()
