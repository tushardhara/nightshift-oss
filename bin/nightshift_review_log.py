#!/usr/bin/env python3
"""Write what a review found into the file the retro scores (ADR-0090, #717).

`.omni/reviews.md` exists so the retro can compute escaped defects -- "the
measure of whether the gate is real or theatre"
(`program/skills/cross-crew-review/SKILL.md`). Until this module, **nothing in
the codebase wrote to it.** The instruction to append a line lived only in
skill prose, so a 12-hour run containing two genuine cross-crew reviews left
the file byte-identical, and an org redesign was then drafted on that empty
reading -- deleting the seats that had found the hardest defects of the run.

Two properties follow directly from that failure and are the reason this
module is shaped the way it is:

1. **The finding outranks the accounting.** A review that ran outside the
   semaphore, or whose task row does not exist in this control plane, still
   gets written down. The missing condition is recorded IN the entry rather
   than used as grounds to drop it. Absence of evidence must never again be
   manufactured by the recorder.
2. **Weaker provenance stays visible, never silently equal.** ADR-0039 proves
   a receipt is bound to a fresh, unedited, correctly-marked PR comment. An
   entry written here without that proof is labelled `local` (or
   `reconstructed`) and carries the label into `review_receipts.source`, so a
   gate can still tell the two apart. Recording more never means proving more.

Module split matches `bin/nightshift_review_receipts.py`: this file validates,
renders, parses and computes. Every `control.db` write stays in
`bin/nightshift-ctl`. The one piece of I/O here is the append to
`.omni/reviews.md` itself, which is not the plane.
"""

import datetime
import errno
import json
import os
import re
import stat


SCHEMA = 1

# The fence the retro parses. Deliberately NOT the ADR-0039
# ```nightshift-review``` fence: that one is a reviewer's assertion inside a
# GitHub comment awaiting validation, this one is an already-validated record
# in our own file. Same-name fences would let a comment body be pasted into
# the log and read back as a validated entry.
BLOCK_FENCE = re.compile(r"```nightshift-review-log\s*\n(.*?)\n```", re.DOTALL)

FULL_SHA = re.compile(r"[0-9a-f]{40}")
# The one spelling of "this entry has no head binding", accepted only from a
# `reconstructed` entry. A historical review whose exact reviewed head is not
# recoverable from the surviving record must say that rather than borrow a
# nearby SHA -- a borrowed SHA asserts a binding that was never checked, and
# would let a reconstruction satisfy an exact-head gate it never passed.
# It cannot be mistaken for a SHA and it will never match one.
HEAD_UNRECOVERABLE = "unrecoverable"
REPO_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
SEAT = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
# Millisecond precision, not seconds. Two `review log` calls 200ms apart used
# to normalize to the SAME second, and "recorded after the pass" is a strict
# comparison -- so an escaped defect logged right after a pass counted zero,
# measured 20 times out of 20 on the default path where `now()` fills the
# field. Sub-second precision is what makes the ordinary sequential case
# order correctly; equal timestamps still mean "order not known" and still
# do not score, which is what the historical replay depends on.
TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$")
DOMAIN_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

SEVERITIES = ("MERGE_BLOCKER", "PRE_DEPLOYMENT", "FOLLOW_UP")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
# What counts as an escape. `FOLLOW_UP` is explicitly out: the review skill
# defines it as pre-existing/unreachable behaviour or unrelated polish, which
# is not a defect that got past a gate. Both blocking severities are counted
# and also reported split, so a reader who disagrees with this line can
# recompute their own number from the same output instead of re-deriving it
# from the file.
BLOCKING = ("MERGE_BLOCKER", "PRE_DEPLOYMENT")

# `unknown` exists for one reason and is restricted to `reconstructed`
# entries: a historical review that demonstrably happened, whose VERDICT is
# not in any surviving record. Without it a reconstruction must invent a pass
# or a fail, and a invented `pass` is worse than either -- it creates the
# `first_pass` every escaped-defect count is measured against. An `unknown`
# verdict mints no receipt (`review_receipts.verdict` is a closed CHECK set)
# and never establishes a pass.
VERDICTS = ("pass", "fail", "unknown")

# Tri-state on purpose, both of them. The attested ADR-0039 path knows the
# domain and the verdict but NOT which seat reviewed which, and ADR-0072
# constraint 4 forbids deriving the vendor from `.omni/models.json` or
# `tasks.crew`. Forcing a boolean there would make every such entry claim
# something the recorder cannot know, and a guessed "independent: true" is
# worse than an empty file. "unknown" is a real answer and must be sayable.
INDEPENDENCE = ("yes", "no", "unknown")

# Where a finding was found, when the entry declares it rather than leaving it
# to be computed from ordering. Every value here means the work had already
# been declared finished to some degree, so every value is an escape.
FOUND_AFTER = ("review_pass", "merge", "deploy")

# What happened to a finding after it was raised. The ticket asks for the
# disposition explicitly, and the run it comes from is why: one reviewer
# finding was overturned (the seat's sandbox could not read outside its own
# worktree, so a file it reported missing was in fact present), one was
# disputed by the author and upheld against them, and a block of them were
# deliberately recorded and not worked. A log that records only severity
# cannot tell those apart, and the escaped-defect count depends on it --
# `overturned` means there was no defect to escape.
DISPOSITIONS = ("open", "accepted", "fixed", "overturned", "upheld",
                "deferred")

PROVENANCES = ("attested", "local", "reconstructed")
SLOT_STATES = ("held", "absent", "unknown")

HEADER = """# Cross-crew review log

Every PR is reviewed by a different crew, round-robin. The retro reads this file to
compute escaped defects — the measure of whether the gate is real or theatre.

Entries are appended by `nightshift-ctl review log` and by `nightshift-ctl review
receipt-record` (ADR-0090). Each one is a table a person reads, followed by a
fenced `nightshift-review-log` block that `nightshift-ctl review escaped-defects`
parses. Do not hand-edit an entry: the fence carries a digest of its own content,
so an edited entry is refused on read rather than silently rescored.
"""


class LogDenied(RuntimeError):
    """Fail-closed refusal which must become exit code 3."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canonical_json(value):
    """Byte-identical discipline to bin/nightshift_review_receipts.py."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


# ------------------------------------------------------------ validation ---

def _text(payload, key, *, required=True, maxlen=4096):
    value = payload.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LogDenied(f"{key}_invalid")
    value = value.strip()
    if len(value) > maxlen:
        raise LogDenied(f"{key}_too_long")
    if "\n" in value and key != "note":
        # Only `note` is a block; everything else is rendered inline into a
        # table cell or a bullet. A newline in those fields let a forged
        # heading and table out of a `summary` -- the same forgery the `note`
        # quoting was added to stop, through the four fields it did not cover.
        raise LogDenied(f"{key}_contains_newline")
    if (any(ord(c) < 0x20 and c != "\n" for c in value)
            or any(c in value for c in ("\x7f", "\u2028", "\u2029", "\u202e"))
            or any(0x80 <= ord(c) <= 0x9f for c in value)):
        # NUL, ANSI escapes, bare CR, the C1 range, the unicode line/paragraph
        # separators and the bidi override are all written verbatim into a
        # file a person reads, so `cat` can be made to display something other
        # than the file's contents.
        raise LogDenied(f"{key}_contains_control_characters")
    if "`" in value and key in ("location", "task_id", "slot_id", "vendor",
                               "domain"):
        # These are rendered INSIDE backticks. Two backticks in the value
        # closes the span and opens a fence for a human reader.
        raise LogDenied(f"{key}_contains_backtick")
    if "```" in value:
        # Free text is rendered ABOVE the fence, so a triple backtick in it
        # opens a code block that swallows the entry's own machine-readable
        # block -- the entry would still be in the file and would silently
        # stop being counted. Refused at write time, where the caller can
        # see it, rather than discovered later as an unexplained gap.
        raise LogDenied(f"{key}_contains_code_fence")
    return value


def _format(moment):
    """ONE spelling per instant. Milliseconds always, even when zero.

    Two spellings is worse than one coarse one. `receipt-record` takes its
    timestamp from a GitHub comment (whole seconds) and `review log` from the
    system clock (fractional), and ranks are compared as STRINGS -- where "."
    (0x2E) sorts before "Z" (0x5A). So `12:00:00.500Z` sorted BEFORE
    `12:00:00Z`, and every mixed pair inside one second ordered backwards:
    the millisecond precision added to stop same-second collisions losing an
    escape instead lost the whole second.

    It also has to round-trip. Emitting `.000Z` and parsing it back to a
    bare `Z` produced a different `entry_id` and the entry read as tampered
    with -- roughly one in a thousand `review log` calls, and every call
    whose `recorded_at` was an integer epoch.
    """
    stamp = moment.astimezone(datetime.timezone.utc)
    return (stamp.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{stamp.microsecond // 1000:03d}Z")


def normalize_timestamp(value):
    """Accept an ISO-8601 instant; return its canonical `...Z` spelling.

    ALWAYS parses. The previous version short-circuited on a regex match and
    returned the caller's string untouched, so `2026-02-30T00:00:00Z`,
    `2026-13-01T…` and `…T99:99:99Z` were all stored verbatim -- shapes that
    are not instants at all but sort lexicographically among real ones and
    silently hide an escape. A regex is a shape check; only the calendar
    knows whether a date exists.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            moment = datetime.datetime.fromtimestamp(
                value, datetime.timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise LogDenied("recorded_at_invalid") from exc
        return _format(moment)
    if not isinstance(value, str) or not value.strip():
        raise LogDenied("recorded_at_invalid")
    raw = value.strip()
    try:
        moment = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LogDenied("recorded_at_invalid") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return _format(moment)


def _validate_finding(raw):
    if not isinstance(raw, dict):
        raise LogDenied("finding_not_object")
    unknown = set(raw) - {"severity", "criterion", "summary", "location",
                          "found_after", "disposition", "disposition_note"}
    if unknown:
        raise LogDenied("finding_key_unknown")
    severity = raw.get("severity")
    if severity not in SEVERITIES:
        raise LogDenied("severity_invalid")
    found_after = raw.get("found_after")
    if found_after is not None and found_after not in FOUND_AFTER:
        raise LogDenied("found_after_invalid")
    disposition = raw.get("disposition", "open")
    if disposition not in DISPOSITIONS:
        raise LogDenied("disposition_invalid")
    note = _text(raw, "disposition_note", required=False, maxlen=4096)
    if disposition in ("overturned", "upheld", "deferred") and not note:
        # These three are the dispositions that CHANGE what the finding
        # counts for, and `overturned` removes it from the escaped-defect
        # total outright. Each has to carry its reason, or the lever exists
        # with nothing attached to it.
        raise LogDenied("disposition_note_missing")
    finding = {
        "severity": severity,
        "criterion": _text(raw, "criterion", required=False),
        "summary": _text(raw, "summary", maxlen=8192),
        "location": _text(raw, "location", required=False),
        "disposition": disposition,
    }
    if note:
        finding["disposition_note"] = note
    if found_after is not None:
        finding["found_after"] = found_after
    return finding


def validate_entry(payload, *, default_recorded_at=None, strict=True):
    """Normalize one review report, or refuse it. Never writes anything.

    `strict=False` is the READ path. It skips the guards that only make sense
    when a caller is writing something new -- currently the
    `domain_outside_vocabulary` contradiction -- so that promoting a domain
    into the canonical vocabulary cannot retroactively make every entry
    already logged under the escape hatch unreadable.
    """
    if not isinstance(payload, dict):
        raise LogDenied("report_not_object")

    domains = _canonical_domains()
    known = {"schema", "recorded_at", "repo", "pr", "task_id", "head_sha",
             "domain", "author_seat", "reviewer_seat", "vendor", "verdict",
             "loops", "independent", "slot_id", "slot_state",
             "slot_absence_reason", "provenance", "comment_url", "marker",
             "note", "findings", "contradictions",
             "domain_outside_vocabulary", "findings_recovered"}
    unknown = set(payload) - known
    if unknown:
        raise LogDenied("report_key_unknown")
    # Recomputed from the entry's own content on every read, so an edited
    # `contradictions` list in the file cannot make a rubber stamp look clean.
    payload = {k: v for k, v in payload.items() if k != "contradictions"}

    schema = payload.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise LogDenied("schema_unsupported")

    recorded_at = payload.get("recorded_at")
    if recorded_at is None:
        if default_recorded_at is None:
            raise LogDenied("recorded_at_invalid")
        recorded_at = default_recorded_at
    recorded_at = normalize_timestamp(recorded_at)

    repo = _text(payload, "repo")
    if not REPO_SLUG.fullmatch(repo):
        raise LogDenied("repo_invalid")

    # Issue #724 / ADR-0092: a review that ran BEFORE a PR existed has no PR
    # number, and refusing it here was the reason early review could not be
    # recorded at all. `None` is accepted only for a `local` entry that names
    # its task -- an attested receipt still needs a PR comment to attest
    # against, and a `reconstructed` replay of history is describing reviews
    # that did have PRs. Absent and null are the same thing; anything else
    # (0, "", "12") is still refused, so this widens the contract by exactly
    # one value rather than loosening the type.
    pr = payload.get("pr")
    if pr is None:
        if payload.get("provenance", "local") != "local":
            raise LogDenied("pr_required_for_this_provenance")
        if not payload.get("task_id"):
            raise LogDenied("pre_pr_review_requires_task_id")
    elif not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
        raise LogDenied("pr_invalid")

    provenance = payload.get("provenance", "local")
    if provenance not in PROVENANCES:
        raise LogDenied("provenance_invalid")

    head_sha = payload.get("head_sha")
    if not isinstance(head_sha, str):
        raise LogDenied("head_sha_invalid")
    if head_sha == HEAD_UNRECOVERABLE:
        if provenance != "reconstructed":
            raise LogDenied("head_sha_invalid")
    elif not FULL_SHA.fullmatch(head_sha):
        raise LogDenied("head_sha_invalid")

    # The ADR-0039 vocabulary has seven domains. An external-repository run
    # produced review kinds it does not name -- architecture and an
    # adversarial attack on it, a rollout/cutover sequence, an on-call
    # runbook, a customer-experience analysis -- and there is already an open
    # PR to add an eighth. Refusing those outright would mean the reviews
    # this vocabulary does not anticipate go unrecorded, which is this
    # ticket's defect narrowed to a tuple.
    #
    # So they are recordable, but only DELIBERATELY: an out-of-vocabulary
    # domain must set `domain_outside_vocabulary`, so a typo like
    # `cross_crew_reveiw` still fails loudly instead of quietly minting a
    # domain that gates nothing. No receipt is minted for one -- the
    # `review_receipts.domain` CHECK constraint is a closed set and this
    # module does not get to widen it -- and `review log` says so by name.
    domain = _text(payload, "domain", maxlen=64)
    outside = payload.get("domain_outside_vocabulary", False)
    if not isinstance(outside, bool):
        raise LogDenied("domain_outside_vocabulary_invalid")
    if domain in domains:
        if outside and strict:
            raise LogDenied("domain_outside_vocabulary_contradicted")
    elif not outside:
        raise LogDenied("domain_unknown")
    elif not DOMAIN_NAME.fullmatch(domain):
        raise LogDenied("domain_invalid")

    verdict = payload.get("verdict")
    if verdict not in VERDICTS:
        raise LogDenied("verdict_invalid")
    if verdict == "unknown" and provenance != "reconstructed":
        raise LogDenied("verdict_invalid")

    independent = payload.get("independent")
    if independent not in INDEPENDENCE:
        raise LogDenied("independent_invalid")

    author_seat = _text(payload, "author_seat", required=False, maxlen=256)
    reviewer_seat = _text(payload, "reviewer_seat", required=False, maxlen=256)
    for seat in (author_seat, reviewer_seat):
        if seat is not None and not SEAT.fullmatch(seat):
            raise LogDenied("seat_invalid")
    # Seats may be omitted ONLY by an entry that also admits it does not know
    # whether the review was independent. Claiming "yes" without naming who
    # reviewed whom is the unfalsifiable form of the claim, and unfalsifiable
    # is what this whole ticket is about.
    if independent != "unknown" and not (author_seat and reviewer_seat):
        raise LogDenied("seat_missing")
    # A seat reading its own work is a fact worth recording and a claim worth
    # refusing. The run this ticket comes from had exactly this case -- the
    # author reviewed the author, on resume-path code, because an authority
    # fence made dispatching any reviewing seat impossible -- and its own
    # delivery record named it honestly. The log must not let a later entry
    # claim otherwise.
    if (independent == "yes" and author_seat
            and reviewer_seat == author_seat):
        raise LogDenied("independence_contradicted")

    loops = payload.get("loops", 1)
    if not isinstance(loops, int) or isinstance(loops, bool) or loops < 1:
        raise LogDenied("loops_invalid")

    slot_state = payload.get("slot_state")
    if slot_state not in SLOT_STATES:
        raise LogDenied("slot_state_invalid")
    slot_id = _text(payload, "slot_id", required=False, maxlen=256)
    slot_absence_reason = _text(payload, "slot_absence_reason",
                                required=False, maxlen=1024)
    if slot_state == "held" and not slot_id:
        raise LogDenied("slot_id_missing")
    if slot_state != "held" and slot_id:
        raise LogDenied("slot_id_contradicts_state")
    if slot_state == "absent" and not slot_absence_reason:
        # AC-3: the missing-slot condition is NOTED, never a reason to drop
        # the entry -- but it must be stated, so "the semaphore was bypassed
        # for reasons nobody wrote down" cannot be recorded as though fine.
        raise LogDenied("slot_absence_reason_missing")

    comment_url = _text(payload, "comment_url", required=False, maxlen=1024)
    marker = _text(payload, "marker", required=False, maxlen=256)
    if provenance == "attested" and not (comment_url and marker):
        raise LogDenied("attestation_incomplete")
    if provenance != "attested" and (comment_url or marker):
        # Carrying an attestation on an unattested entry is how a `local`
        # row starts reading like an `attested` one three months later.
        raise LogDenied("attestation_unearned")

    # An empty list means "this review found nothing". A reconstruction whose
    # findings are simply not in any surviving record must be able to say so:
    # the previous version rendered both as `0 MERGE_BLOCKER, 0 …` and wrote
    # `"findings":[]` into the fence, which encodes NOT RECOVERED as ZERO in
    # the half the retro actually reads -- a blank read as a zero, inside the
    # replay for the ticket about blanks read as zeros.
    recovered = payload.get("findings_recovered", True)
    if not isinstance(recovered, bool):
        raise LogDenied("findings_recovered_invalid")
    if not recovered and provenance != "reconstructed":
        raise LogDenied("findings_recovered_invalid")
    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        raise LogDenied("findings_invalid")
    if not recovered and findings:
        raise LogDenied("findings_recovered_contradicted")
    if len(findings) > 2048:
        raise LogDenied("findings_too_many")
    validated = [_validate_finding(item) for item in findings]

    # Contradictions are COMPUTED and RECORDED, never grounds to refuse. A
    # `pass` carrying an unresolved MERGE_BLOCKER contradicts the review
    # contract (release gates on zero MERGE_BLOCKERs for the exact head) and
    # is the shape a rubber stamp takes -- so it must end up in the file where
    # the retro can see it, not bounced back to a caller who will then record
    # nothing at all. Refusing here would reproduce this ticket's own defect.
    contradictions = []
    if verdict == "pass" and any(
            f["severity"] == "MERGE_BLOCKER"
            and f["disposition"] not in ("fixed", "overturned")
            for f in validated):
        # "Unresolved" now means unresolved. The check used to test severity
        # alone, so a `pass` whose blocker was already fixed was flagged
        # identically to a rubber stamp -- and the ADR claimed otherwise.
        contradictions.append("pass_with_merge_blocker")
    if author_seat and reviewer_seat == author_seat:
        # `independent: "yes"` is already refused above; this catches the
        # entry that names one seat twice and declines to characterise it.
        contradictions.append("self_review")

    entry = {
        "schema": SCHEMA,
        "recorded_at": recorded_at,
        "repo": repo,
        "pr": pr,
        "task_id": _text(payload, "task_id", maxlen=256),
        "head_sha": head_sha,
        "domain": domain,
        "domain_outside_vocabulary": bool(outside),
        "findings_recovered": bool(recovered),
        "verdict": verdict,
        "loops": loops,
        "independent": independent,
        "slot_state": slot_state,
        "provenance": provenance,
        "contradictions": contradictions,
        "findings": validated,
    }
    for key, value in (("vendor", _text(payload, "vendor", required=False,
                                          maxlen=128)),
                       ("author_seat", author_seat),
                       ("reviewer_seat", reviewer_seat),
                       ("slot_id", slot_id),
                       ("slot_absence_reason", slot_absence_reason),
                       ("comment_url", comment_url),
                       ("marker", marker),
                       ("note", _text(payload, "note", required=False,
                                      maxlen=8192))):
        if value:
            entry[key] = value
    entry["entry_id"] = _entry_id(entry)
    return entry


def _canonical_domains():
    """The ADR-0039 vocabulary, imported rather than copied.

    Falls back only when the receipts module cannot be imported at all, which
    on this bundle means a broken checkout; refusing every domain then is the
    fail-closed answer, not a second hardcoded tuple that can drift.
    """
    try:
        import nightshift_review_receipts as receipts
        # `AttributeError` too: the accessor is newer than the module it lives
        # in, so a mixed-version deployment would otherwise raise an uncaught
        # exit-1 traceback where the documented answer is a fail-closed
        # refusal. The attribute it replaced never could.
        return receipts.canonical_domains()
    except (ImportError, OSError, SyntaxError, AttributeError) as exc:
        raise LogDenied("domain_vocabulary_unavailable") from exc


def pr_by_task(entries):
    """Which task ended up on which PR, learned from the log itself.

    A review that ran before the PR existed and the PR's own reviews are
    reviews OF THE SAME WORK, and the escaped-defect metric only means
    anything if they share a unit. Keeping them apart makes the instrument
    structurally unable to answer the question #716 asked -- did reviewing
    earlier catch things? -- because a defect that passed a pre-PR review and
    then appeared at the PR would sit in two disjoint buckets and score zero.
    It also double-counts one defect reported twice, and puts an `overturned`
    disposition recorded at the PR out of reach of the pre-PR escape it
    retracts.

    The mapping comes from entries that HAVE a PR, so it costs nothing on a
    log that has no pre-PR entries at all: every unit resolves to the same
    `(repo, pr)` it always did.
    """
    mapping = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if entry.get("pr") is not None and task_id:
            mapping.setdefault((entry["repo"], task_id), entry["pr"])
    return mapping


def review_unit(entry, pr_map=None):
    """The unit a review is OF, for counting: a PR, or a task not yet on one.

    Keying a PR-less entry on `(repo, None)` would merge every pre-PR review
    in a repository into one unit -- one "PR" reviewed many times and passing
    once, which does not lose an escape but FABRICATES one. The `task:` prefix
    keeps a task id from ever colliding with a PR number.
    """
    if entry.get("pr") is not None:
        return (entry["repo"], entry["pr"])
    task_id = entry.get("task_id") or ""
    if pr_map:
        known = pr_map.get((entry["repo"], task_id))
        if known is not None:
            # The PR exists elsewhere in this log: same work, same unit.
            return (entry["repo"], known)
    return (entry["repo"], "task:" + str(task_id))


def _entry_id(entry):
    import hashlib
    payload = {k: v for k, v in entry.items()
               if k != "entry_id" and not k.startswith("_")}
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def fingerprint(criterion, summary):
    """Same content-addressed identity `review consolidate` already uses."""
    import nightshift_review_receipts as receipts
    return receipts.fingerprint(criterion, summary)


# ------------------------------------------------------------- rendering ---

def _code(value, fallback):
    return f"`{value}`" if value else fallback


def _severity_counts(findings):
    counts = {name: 0 for name in SEVERITIES}
    for finding in findings:
        counts[finding["severity"]] += 1
    return counts


def render_entry(entry):
    """One entry as markdown: readable by a person, parseable by the retro.

    Both halves are required. The prose is why the file is worth opening; the
    fence is why `review escaped-defects` cannot quietly read zero out of a
    file full of reviews.
    """
    counts = _severity_counts(entry["findings"])
    # Free text is rendered into the region a person reads, and markdown has
    # no escaping. A `note` containing a `---` rule, a `###` heading and a
    # table forged a complete, visually authentic second entry -- an attested
    # `security` pass by a seat that does not exist -- from one `review log`
    # call. Quoting every line neutralises headings, rules and tables at once,
    # and leaves the text readable.
    def quoted(text):
        return "\n".join("> " + line if line else ">"
                          for line in text.splitlines())

    if entry["slot_state"] == "held":
        slot = f"held (`{entry['slot_id']}`)"
    elif entry["slot_state"] == "absent":
        slot = f"**absent** — {entry.get('slot_absence_reason', '')}"
    else:
        slot = "**unknown** — the recorder could not observe it"
    independent = {"yes": "yes", "no": "**NO**",
                   "unknown": "**unknown**"}[entry["independent"]]
    unknown = "_not recorded_"
    lines = [
        "",
        "---",
        "",
        f"### {entry['recorded_at'][:10]} · `{entry['repo']}` "
        + (f"PR #{entry['pr']}" if entry["pr"] is not None
           else f"before any PR · task `{entry['task_id']}`")
        + f" · `{entry['domain']}`"
        + (" (outside the frozen review vocabulary — no receipt)"
           if entry["domain_outside_vocabulary"] else ""),
        "",
        "| field | value |",
        "|---|---|",
        f"| task | `{entry['task_id']}` |",
        f"| head | `{entry['head_sha']}` |",
        f"| reviewed seat | {_code(entry.get('author_seat'), unknown)} |",
        f"| reviewing seat | {_code(entry.get('reviewer_seat'), unknown)} |",
        f"| vendor | {entry.get('vendor') or unknown} |",
        f"| verdict | **{entry['verdict']}** |",
        f"| loops | {entry['loops']} |",
        f"| independent | {independent} |",
        f"| slot | {slot} |",
        f"| provenance | `{entry['provenance']}` |",
        f"| entry | `{entry['entry_id']}` |",
    ]
    if entry.get("comment_url"):
        lines.append(f"| attestation | {entry['comment_url']} |")
    lines.append("")
    if entry["contradictions"]:
        lines.append("**Contradictions recorded rather than refused:** "
                     + ", ".join(f"`{name}`"
                                 for name in entry["contradictions"]))
        lines.append("")
    if not entry["findings_recovered"]:
        lines.append("Findings — **NOT RECOVERED**. This entry records that "
                     "the review happened; its findings are not in any "
                     "surviving record and are not zero.")
    else:
        lines.append(
            "Findings — "
            f"{counts['MERGE_BLOCKER']} MERGE_BLOCKER, "
            f"{counts['PRE_DEPLOYMENT']} PRE_DEPLOYMENT, "
            f"{counts['FOLLOW_UP']} FOLLOW_UP:")
    lines.append("")
    if entry["findings_recovered"] and not entry["findings"]:
        lines.append("- none found")
    for finding in entry["findings"]:
        where = f" `{finding['location']}` —" if finding.get("location") else ""
        after = (f" _(found after {finding['found_after']})_"
                 if finding.get("found_after") else "")
        lines.append(f"- **{finding['severity']}** —{where} "
                     f"{finding['summary']}{after}")
        if finding.get("criterion"):
            lines.append(f"  - criterion: {finding['criterion']}")
        disposition = f"  - disposition: **{finding['disposition']}**"
        if finding.get("disposition_note"):
            disposition += f" — {finding['disposition_note']}"
        lines.append(disposition)
    if entry.get("note"):
        lines.append("")
        lines.append(quoted(entry["note"]))
    lines.append("")
    lines.append("```nightshift-review-log")
    lines.append(canonical_json(
        {k: v for k, v in entry.items() if not k.startswith("_")}))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------- the file --

def _open_log_for_append(path):
    """An append descriptor on `path` that cannot have been redirected.

    Audit C3: `.omni/` lives in the TARGET repository, which can track
    `.omni/reviews.md` -- or `.omni` itself -- as a symlink to `~/.bashrc`,
    and review text then lands there. This used to follow symlinks on
    purpose so a log symlinked into shared storage kept working. Security
    wins: a symlink at either level is refused. To keep the log elsewhere,
    pass that real path explicitly instead of linking to it.

    The directory is opened `O_NOFOLLOW` and must be a real directory owned
    by this uid; the file is then opened relative to that descriptor with
    `O_NOFOLLOW`. `O_NONBLOCK` keeps a fifo from hanging the open -- the
    caller holds `BEGIN IMMEDIATE`, so a hang wedges the control plane.
    """
    directory, name = os.path.split(os.path.abspath(path))
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise LogDenied("reviews_dir_missing") from exc
    except OSError as exc:
        raise LogDenied("reviews_dir_untrusted") from exc
    try:
        try:
            target = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            target = None
        except OSError as exc:
            raise LogDenied("reviews_unwritable") from exc
        if target is not None and stat.S_ISLNK(target.st_mode):
            raise LogDenied("reviews_is_a_symlink")
        if target is not None and not stat.S_ISREG(target.st_mode):
            raise LogDenied("reviews_not_a_regular_file")
        info = os.fstat(dir_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise LogDenied("reviews_dir_untrusted")
        flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
                 | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        try:
            handle = os.open(name, flags, 0o644, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:    # a symlink swapped in after lstat
                raise LogDenied("reviews_is_a_symlink") from exc
            if exc.errno == errno.ENXIO:    # a fifo with no reader
                raise LogDenied("reviews_not_a_regular_file") from exc
            raise LogDenied("reviews_unwritable") from exc
    finally:
        os.close(dir_fd)
    return handle


def append_entry(path, entry):
    """Append one rendered entry. Returns the rendered text.

    Uses a single `O_APPEND` write rather than read-modify-write on purpose:
    several seats can be finishing reviews at once, and read-modify-write
    loses one of them silently -- which is this ticket's own defect wearing a
    different hat. `O_APPEND` makes the offset update atomic, so concurrent
    writers interleave at worst, never overwrite.
    """
    text = render_entry(entry)
    handle = _open_log_for_append(path)
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode):  # raced between stat and open
            raise LogDenied("reviews_not_a_regular_file")
        # One `O_APPEND` write, header included. The previous version created
        # the file on a separate non-append fd and let Python buffer the
        # header to offset 0, so a concurrent appender's entry could be
        # overwritten by the header landing on top of it -- both writers
        # reporting success, one review gone.
        payload = (HEADER + text if info.st_size == 0 else text)
        raw = payload.encode("utf-8")
        written = os.write(handle, raw)
        if written != len(raw):
            # A short write (ENOSPC returns a partial count, not an error)
            # leaves a half-entry that poisons the file for every later read.
            raise LogDenied("reviews_write_truncated")
        os.fsync(handle)
    except OSError as exc:
        raise LogDenied("reviews_unwritable") from exc
    finally:
        os.close(handle)
    return text


def read_log(path):
    """Everything the log says, including what it says badly.

    Returns `{entries, unreadable, legacy_rows, headings, openers}`. Nothing
    is dropped silently: an entry that cannot be read is REPORTED with its
    reason instead of vanishing, and instead of taking the whole file down.
    Both halves of that matter. Raising on one bad entry silenced the metric
    for every wave; skipping it silently is the reading this ticket exists to
    prevent. Counting it out loud is neither.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError as exc:
        raise LogDenied("reviews_missing") from exc
    except OSError as exc:
        raise LogDenied("reviews_unreadable") from exc
    except UnicodeDecodeError as exc:
        # A `ValueError`, not an `OSError`, so it escaped as an exit-1
        # traceback. `append_entry` itself anticipates a short write and
        # concurrent interleaving, either of which can split a multi-byte
        # sequence -- after which every future retro run crashed, permanently.
        raise LogDenied("reviews_not_utf8") from exc
    return parse_log(text)


def read_entries(path):
    """Back-compat: just the readable entries, oldest first."""
    return read_log(path)["entries"]


# A pre-ADR-0090 row: `| 2026-08-23 | #15 (...) | crew_beta | ...`, and the
# prose entries that followed the table. These are REAL RECORDED REVIEWS that
# this parser cannot score. Counting them is what stops `escaped-defects`
# from reporting "nobody recorded anything" about a file full of reviews --
# which is this ticket's own defect, and which the first version of this
# module committed against 630 lines of genuine record.
LEGACY_ROW = re.compile(r"^[ \t]*\|\s*\d{4}-\d{2}-\d{2}\s*\|", re.M)
# The negative lookahead matters: an ADR-0090 entry heading is
# `### YYYY-MM-DD · `, which without it matches this pattern too and
# reports every new entry as a legacy row -- inflating exactly the
# count that is supposed to tell a reader the file predates the format.
LEGACY_HEADING = re.compile(
    r"^[ \t]*#{2,6}\s+\d{4}-\d{2}-\d{2}(?! · )", re.M)
FENCE_OPENER = re.compile(r"^```nightshift-review-log\s*$", re.M)
ENTRY_HEADING = re.compile(r"^### \d{4}-\d{2}-\d{2} · ", re.M)


def parse_log(text):
    entries = []
    unreadable = []
    seen_ids = {}
    duplicates = 0
    for index, block in enumerate(BLOCK_FENCE.findall(text)):
        try:
            entry = _parse_one(block)
        except LogDenied as exc:
            unreadable.append({"index": index, "reason": exc.reason})
            continue
        if entry["entry_id"] in seen_ids:
            # `receipt-record` appends unconditionally but upserts its row, so
            # re-running it after a transient failure used to double-count
            # every number the retro quotes. Identical entries are one entry.
            duplicates += 1
            continue
        seen_ids[entry["entry_id"]] = True
        entry["_ordinal"] = index
        entries.append(entry)
    entries.sort(key=lambda item: (item["recorded_at"], item["_ordinal"]))

    openers = len(FENCE_OPENER.findall(text))
    parsed = len(entries) + len(unreadable) + duplicates
    if openers > parsed:
        # A fence opener with no closing fence: the file was truncated
        # mid-append, or an entry's block was deleted by hand. Both used to
        # rescore silently to a smaller number with exit 0.
        unreadable.append({"index": -1, "reason": "unterminated_fence",
                           "count": openers - parsed})
    headings = len(ENTRY_HEADING.findall(text))
    return {
        "entries": entries,
        "unreadable": unreadable,
        "duplicates": duplicates,
        "headings": headings,
        "fences": openers,
        "legacy_rows": (len(LEGACY_ROW.findall(text))
                        + len(LEGACY_HEADING.findall(text))),
    }


def parse_entries(text):
    """Strict form: any unreadable entry is an error, as before."""
    result = parse_log(text)
    if result["unreadable"]:
        first = result["unreadable"][0]
        raise LogDenied(f"entry_{first['index']}_{first['reason']}")
    return result["entries"]


def _parse_one(block):
    try:
        payload = json.loads(block)
    except ValueError as exc:
        raise LogDenied("unparseable") from exc
    if not isinstance(payload, dict):
        raise LogDenied("not_object")
    claimed = payload.pop("entry_id", None)
    # `strict=False`: the write-time guards must not run on read. The
    # `domain_outside_vocabulary` contradiction check is the one that bites --
    # the moment a domain is promoted into the canonical set (there is an open
    # PR to do exactly that), every entry already logged under the escape
    # hatch would start refusing, and the whole file with it, permanently,
    # because entries cannot be edited without breaking their own digest.
    entry = validate_entry(payload, strict=False)
    if claimed is not None and claimed != entry["entry_id"]:
        # The entry was edited after it was written. Say so rather than
        # scoring the edited version as though it were the recorded one.
        raise LogDenied("id_mismatch")
    return entry


# ------------------------------------------------------- escaped defects ---

def escape_fingerprint(finding, head_sha):
    """Identity of an escaped defect. NOT `review consolidate`'s fingerprint.

    That one is deliberately `f(criterion, summary)` with domain excluded, so
    two domains reporting one defect at one head consolidate into one row --
    correct for consolidation, wrong here. Reused unchanged it also collapsed
    two blockers at DIFFERENT LOCATIONS and two blockers on DIFFERENT HEADS
    into a single escape, and dropped the second location from the report
    entirely. Location and head are part of the identity; domain still is not.
    """
    import nightshift_review_receipts as receipts
    return receipts.digest(canonical_json({
        "criterion": (finding.get("criterion") or "").casefold().strip(),
        "summary": finding["summary"].casefold().strip(),
        "location": (finding.get("location") or "").casefold().strip(),
        "head_sha": head_sha,
    }))


def escaped_defects(entries, *, attested_receipts=None):
    """Blocking findings recorded after that PR was already passed.

    Two sources, deliberately:

    - **declared** -- the entry says `found_after`, because the finder knew
      the work had been called done (a post-merge staff audit, an incident, a
      bot reviewing an approved head). Ordering cannot infer that.
    - **computed** -- a blocking finding whose entry is later than the first
      `pass` on the same PR. This is the case nobody remembers to declare.

    Ordering uses `recorded_at` and, for entries sharing an instant, the order
    they appear in the file. Equal timestamps with no file order to separate
    them are treated as concurrent and do NOT score -- an invented ordering
    would manufacture an escape nobody observed.
    """
    # One mapping for the whole computation: a pre-PR review and the PR's own
    # reviews are reviews of the same work and must share a unit, or an escape
    # across that boundary cannot be scored and one defect reported twice is
    # counted twice.
    pr_map = pr_by_task(entries)

    first_pass = {}
    for entry in entries:
        if entry["verdict"] != "pass":
            continue
        key = review_unit(entry, pr_map)
        rank = (entry["recorded_at"], entry.get("_ordinal", 0))
        if key not in first_pass or rank < first_pass[key]:
            first_pass[key] = rank

    # Disposition is resolved across the WHOLE log before anything is
    # counted. Entries are immutable by design, so an overturn discovered
    # after the review was recorded can only arrive as a later entry naming
    # the same finding -- which the previous version ignored, making the one
    # reachable case of `overturned` unreachable.
    # Disposition is resolved on identity WITHOUT the head, because an
    # overturn is normally recorded at a later head than the finding it
    # retracts -- exactly the case the cross-crew skill describes. Keyed with
    # the head, an honest overturn both failed to clear the escape and
    # incremented `excluded_overturned`: one finding counted as an escape and
    # a suppressed escape at the same time.
    latest_disposition = {}
    for entry in entries:
        for finding in entry["findings"]:
            if finding["severity"] not in BLOCKING:
                # Severity stays OUT of the identity, so `consolidate_findings`
                # and this agree that one defect read two ways is one defect.
                # The cost is that a re-report of the same text as a
                # `FOLLOW_UP` shares the mark -- and marked `overturned` it
                # cancelled a MERGE_BLOCKER escape, pulling the one lever that
                # lowers this number without anything being fixed from a
                # finding that was never blocking. Only a blocking finding
                # gets a say in a blocking finding's disposition.
                continue
            mark = (review_unit(entry, pr_map),
                    escape_fingerprint(finding, ""))
            rank = (entry["recorded_at"], entry.get("_ordinal", 0))
            known = latest_disposition.get(mark)
            if known is None or rank >= known[0]:
                latest_disposition[mark] = (rank, finding["disposition"])

    seen = set()
    defects = []
    overturned = 0
    for entry in entries:
        key = review_unit(entry, pr_map)
        passed_at = first_pass.get(key)
        rank = (entry["recorded_at"], entry.get("_ordinal", 0))
        for finding in entry["findings"]:
            if finding["severity"] not in BLOCKING:
                continue
            mark = (key, escape_fingerprint(finding, entry["head_sha"]))
            disposition_mark = (key, escape_fingerprint(finding, ""))
            declared = finding.get("found_after")
            after_pass = passed_at is not None and rank > passed_at
            if not declared and not after_pass:
                continue
            if latest_disposition.get(
                    disposition_mark, (None, "open"))[1] == "overturned":
                # There was no defect to escape. Counted separately rather
                # than dropped: `overturned` is the one lever that lowers
                # this number without fixing anything, so its use has to be
                # as visible as the total it changes.
                if mark not in seen:
                    seen.add(mark)
                    overturned += 1
                continue
            if mark in seen:
                # Same defect, reported again. Keep the most severe reading,
                # matching `consolidate_findings`, which the previous
                # first-seen-wins rule contradicted on identical data.
                for prior in defects:
                    if prior["_mark"] == mark:
                        if (SEVERITY_RANK[finding["severity"]]
                                < SEVERITY_RANK[prior["severity"]]):
                            prior["severity"] = finding["severity"]
                        if entry["domain"] not in prior["domains"]:
                            prior["domains"].append(entry["domain"])
                        # `recorded_at` stays the FIRST sighting (when it
                        # escaped); `last_recorded_at` moves, and it is what a
                        # window filters on -- otherwise a defect re-reported
                        # inside the wave was excluded because its first
                        # sighting predated the window.
                        prior["last_recorded_at"] = entry["recorded_at"]
                        break
                continue
            seen.add(mark)
            defects.append({
                "_mark": mark,
                "repo": entry["repo"],
                "pr": entry["pr"],
                "head_sha": entry["head_sha"],
                "domain": entry["domain"],
                "domains": [entry["domain"]],
                "reviewer_seat": entry.get("reviewer_seat"),
                "severity": finding["severity"],
                "summary": finding["summary"],
                "location": finding.get("location"),
                "disposition": latest_disposition.get(
                    disposition_mark, (None, finding["disposition"]))[1],
                "fingerprint": mark[1],
                "recorded_at": entry["recorded_at"],
                "last_recorded_at": entry["recorded_at"],
                "passed_at": passed_at[0] if passed_at else None,
                "reason": f"declared:{declared}" if declared else "after_pass",
            })
    for defect in defects:
        defect.pop("_mark", None)

    by_severity = {name: 0 for name in BLOCKING}
    for defect in defects:
        by_severity[defect["severity"]] += 1
    # Sorted by a stringified key rather than by the tuple: a PR unit's second
    # element is an int and a pre-PR unit's is a string, and comparing them
    # raises. The VALUES are left as they are, so a report over PR-only entries
    # is byte-identical to what it was before pre-PR entries existed.
    reviewed = sorted({review_unit(entry, pr_map) for entry in entries},
                      key=lambda unit: (unit[0], str(unit[1])))
    # Reported every time the number is, because the number means different
    # things depending on them. #716's finding was that no claim about
    # reviewer value was evidence-backed; these counts are how strong the
    # evidence behind THIS reading is, and printing them beside the total is
    # cheaper than remembering to ask.
    by_provenance = {name: 0 for name in PROVENANCES}
    by_independence = {name: 0 for name in INDEPENDENCE}
    findings_not_recovered = 0
    unverified_attested = 0
    for entry in entries:
        by_provenance[entry["provenance"]] += 1
        by_independence[entry["independent"]] += 1
        if not entry["findings_recovered"]:
            findings_not_recovered += 1
        if (entry["provenance"] == "attested"
                and attested_receipts is not None
                and (entry["task_id"], entry["head_sha"], entry["domain"])
                not in attested_receipts):
            # The file is ordinary local state: anyone who can write it can
            # write `"provenance":"attested"` and compute the matching
            # `entry_id`, which is a digest and not a signature. The control
            # plane is the only corroboration available, so an `attested`
            # entry with no `source='attested'` receipt behind it is counted
            # and named rather than believed.
            unverified_attested += 1
    return {
        "entries": len(entries),
        "prs_reviewed": len(reviewed),
        "prs_passed": len(first_pass),
        "escaped": len(defects),
        "escaped_by_severity": by_severity,
        "excluded_overturned": overturned,
        "entries_by_provenance": by_provenance,
        "entries_by_independence": by_independence,
        "entries_with_findings_not_recovered": findings_not_recovered,
        "unverified_attested_entries": unverified_attested,
        "contradictions": sorted(
            {name for entry in entries for name in entry["contradictions"]}),
        "defects": defects,
    }
