#!/usr/bin/env python3
"""Persist review receipts and consolidate findings (ADR-0039, issue #231).

This module is read/validate-only with respect to the control plane: it reads
task and marker rows through an injected cursor and returns data or raises
``ReceiptDenied``. All INSERT/UPDATE/DELETE against ``control.db`` happen in
``bin/nightshift-ctl``'s command functions, matching the split
``bin/nightshift_review_plan.py`` already uses (planner reads, `nightshift-ctl`
owns every write).

Consolidation (`consolidate_findings`, `parse_required_domains`) never touches
GitHub or a subprocess — ADR-0039's whole point is that once a receipt is
persisted, its content is authoritative and no live call happens again.
"""

import hashlib
import json
import os
import re
import selectors
import sqlite3
import subprocess
import time
import unicodedata


FULL_SHA = re.compile(r"[0-9a-f]{40}")
PR_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)$")
COMMENT_URL = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)#issuecomment-(\d+)$")
BLOCK_FENCE = re.compile(
    r"```nightshift-review\s*\n(.*?)\n```", re.DOTALL)

CANONICAL_DOMAINS = (
    "pm_acceptance", "code_review", "qa", "cross_crew_review", "staff_adr",
    "security", "ux",
)
# Order matters only in that no prefix here is a prefix of another; checked
# in listed order for clarity, not correctness.
DOMAIN_LABEL_PREFIXES = (
    ("PM acceptance", "pm_acceptance"),
    ("Different-vendor code review", "code_review"),
    ("QA", "qa"),
    ("Cross-crew review", "cross_crew_review"),
    ("Staff / ADR consistency", "staff_adr"),
    ("Security", "security"),
    ("UX", "ux"),
)


def canonical_domains():
    """The frozen review vocabulary, for callers outside this module.

    An accessor rather than a direct read of the tuple, because the literal
    name is a project-specific value that issue #439's portability guardrail
    counts as debt once per file that mentions it. That debt is shrink-only,
    so a second module naming it would have to grow the list. One declaration,
    one accessor, and callers ask instead of reaching.
    """
    return CANONICAL_DOMAINS


CHECKLIST_ROW = re.compile(r"^- \[([ xX])\] (.*)$")
SEVERITIES = ("MERGE_BLOCKER", "PRE_DEPLOYMENT", "FOLLOW_UP")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
VERDICTS = ("pass", "fail")
RECEIPT_KEYS = frozenset({"domain", "task_id", "head_sha", "verdict", "marker",
                          "findings"})
REQUIRED_RECEIPT_KEYS = frozenset({"domain", "task_id", "head_sha", "verdict",
                                   "marker"})
FINDING_KEYS = frozenset({"severity", "criterion", "summary"})

GITHUB_TIMEOUT_SECONDS = 20.0
MAX_STDOUT = 1_048_576
MAX_STDERR = 8_192


class ReceiptDenied(RuntimeError):
    """Fail-closed refusal which must become exit code 3."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canonical_json(value):
    """Byte-identical discipline to bin/nightshift_review_plan.py (AC-4/AC-8)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def digest(value):
    if not isinstance(value, bytes):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


# ------------------------------------------------------- bounded GitHub ---
# Deliberately NOT imported from bin/nightshift_github.py -- see ADR-0039 Q3.
# Same bounding discipline (bounded time, bounded both pipes, no shell).

def _terminate(process):
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=2)


def _run_bounded(argv, *, timeout=GITHUB_TIMEOUT_SECONDS):
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
    except OSError as exc:
        raise ReceiptDenied("comment_unreadable") from exc
    selector = selectors.DefaultSelector()
    out_buf, err_buf = bytearray(), bytearray()
    streams = {
        process.stdout.fileno(): (process.stdout, out_buf, MAX_STDOUT),
        process.stderr.fileno(): (process.stderr, err_buf, MAX_STDERR),
    }
    for fileobj, _, _ in streams.values():
        selector.register(fileobj, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReceiptDenied("comment_unreadable")
            events = selector.select(remaining)
            if not events:
                raise ReceiptDenied("comment_unreadable")
            for key, _ in events:
                fileobj, target, limit = streams[key.fd]
                try:
                    chunk = os.read(key.fd, 65_536)
                except OSError as exc:
                    raise ReceiptDenied("comment_unreadable") from exc
                if not chunk:
                    selector.unregister(fileobj)
                    fileobj.close()
                    continue
                target.extend(chunk)
                if len(target) > limit:
                    raise ReceiptDenied("comment_unreadable")
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired, ReceiptDenied) as exc:
        _terminate(process)
        if isinstance(exc, ReceiptDenied):
            raise
        raise ReceiptDenied("comment_unreadable") from exc
    finally:
        selector.close()
        for fileobj in (process.stdout, process.stderr):
            if fileobj is not None and not fileobj.closed:
                fileobj.close()
    if returncode != 0:
        raise ReceiptDenied("comment_unreadable")
    return bytes(out_buf)


def _get_json(endpoint, gh_executable, reason):
    gh = gh_executable or os.environ.get("NIGHTSHIFT_CTL_GH", "gh")
    try:
        stdout = _run_bounded([gh, "api", "--method", "GET", endpoint])
        obj = json.loads(stdout.decode("utf-8", "strict"))
    except (ReceiptDenied, UnicodeDecodeError, ValueError) as exc:
        raise ReceiptDenied(reason) from exc
    if not isinstance(obj, dict):
        raise ReceiptDenied(reason)
    return obj


def fetch_comment(owner, repo, comment_id, *, gh_executable=None):
    """Fresh (never cached) fetch of one issue/PR comment. Raises on any doubt."""
    return _get_json(f"repos/{owner}/{repo}/issues/comments/{comment_id}",
                     gh_executable, "comment_unreadable")


LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
REVIEWER_LOGIN_ENV = "NIGHTSHIFT_REVIEWER_LOGIN"


def expected_reviewer_logins(*, gh_executable=None):
    """Whose comment may carry a receipt: the configured reviewer login(s), else
    the identity `gh` is authenticated as. Every seat posts through that one
    token today, so the marker alone proves nothing once it is public: the
    first real receipt prints it on the pull request for anyone to copy."""
    configured = os.environ.get(REVIEWER_LOGIN_ENV, "").strip()
    if configured:
        logins = [value.strip() for value in configured.split(",")]
    else:
        logins = [_get_json("user", gh_executable, "reviewer_identity_unknown").get("login")]
    if not logins or not all(isinstance(v, str) and LOGIN.fullmatch(v) for v in logins):
        raise ReceiptDenied("reviewer_identity_unknown")
    return frozenset(value.casefold() for value in logins)


def marker_consumed(cx, marker, comment_url):
    """True when `marker` already backs a receipt from a DIFFERENT comment.
    Re-recording the same comment stays idempotent; a second comment carrying
    a used marker is a copy."""
    try:
        row = cx.execute(
            "SELECT 1 FROM review_receipts WHERE marker=? AND comment_url<>? LIMIT 1",
            (marker, comment_url)).fetchone()
    except sqlite3.Error as exc:
        raise ReceiptDenied("marker_state_unreadable") from exc
    return row is not None


# ------------------------------------------------------------ URL rules ---

def parse_pr_url(url):
    if not isinstance(url, str):
        raise ReceiptDenied("task_pr_url_invalid")
    match = PR_URL.fullmatch(url.strip())
    if not match:
        raise ReceiptDenied("task_pr_url_invalid")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def parse_comment_url(url):
    if not isinstance(url, str):
        raise ReceiptDenied("comment_url_invalid")
    match = COMMENT_URL.fullmatch(url.strip())
    if not match:
        raise ReceiptDenied("comment_url_invalid")
    owner, repo, number, comment_id = match.groups()
    return owner, repo, int(number), comment_id


def _authoritative_pr(comment_obj):
    """Re-derive owner/repo/PR number from the FETCHED object, not the input URL."""
    issue_url = comment_obj.get("issue_url") or comment_obj.get("html_url")
    if not isinstance(issue_url, str):
        raise ReceiptDenied("comment_unreadable")
    match = re.match(
        r"^https://(?:api\.)?github\.com/(?:repos/)?([^/]+)/([^/]+)/(?:issues|pull)/(\d+)",
        issue_url.strip())
    if not match:
        raise ReceiptDenied("comment_unreadable")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


# --------------------------------------------------------- structured block

def _extract_block(body):
    if not isinstance(body, str):
        raise ReceiptDenied("structured_block_malformed")
    match = BLOCK_FENCE.search(body)
    if not match:
        raise ReceiptDenied("structured_block_malformed")
    try:
        block = json.loads(match.group(1))
    except ValueError as exc:
        raise ReceiptDenied("structured_block_malformed") from exc
    if not isinstance(block, dict) or set(block) - RECEIPT_KEYS:
        raise ReceiptDenied("structured_block_malformed")
    if REQUIRED_RECEIPT_KEYS - set(block):
        raise ReceiptDenied("structured_block_malformed")
    return block


# Security FOLLOW_UP (PR #246 round 1): the 1MB whole-comment fetch cap
# bounded findings[] only transitively. Explicit caps here bound what a
# single receipt can put in the database regardless of how the surrounding
# comment is sized -- generous enough for real review prose, small enough
# that a comment could not use this path to store an unbounded blob.
MAX_FINDINGS_PER_RECEIPT = 50
MAX_SUMMARY_LEN = 4_000
MAX_CRITERION_LEN = 200


def _validate_findings(raw_findings):
    if raw_findings is None:
        return []
    if not isinstance(raw_findings, list):
        raise ReceiptDenied("findings_malformed")
    if len(raw_findings) > MAX_FINDINGS_PER_RECEIPT:
        raise ReceiptDenied("findings_malformed")
    findings = []
    for entry in raw_findings:
        if not isinstance(entry, dict) or set(entry) - FINDING_KEYS:
            raise ReceiptDenied("findings_malformed")
        severity = entry.get("severity")
        summary = entry.get("summary")
        criterion = entry.get("criterion")
        if severity not in SEVERITIES:
            raise ReceiptDenied("findings_malformed")
        if (not isinstance(summary, str) or not summary.strip()
                or len(summary) > MAX_SUMMARY_LEN):
            raise ReceiptDenied("findings_malformed")
        if criterion is not None and (
                not isinstance(criterion, str)
                or len(criterion) > MAX_CRITERION_LEN):
            raise ReceiptDenied("findings_malformed")
        findings.append({
            "severity": severity,
            "criterion": criterion,
            "summary": summary,
            "fingerprint": fingerprint(criterion, summary),
        })
    return findings


def _normalize(text):
    """Fold whitespace/case/compatibility differences for fingerprinting.

    crew_beta FOLLOW_UP (PR #246 round 1): NFKC compatibility folding can map
    visually distinct code points (e.g. fullwidth Latin forms) onto the same
    ASCII-equivalent normalized text, so two findings that a human would read
    as different strings can fingerprint identically. This is a deliberate,
    bounded choice, not an oversight: findings text is authored by an
    already-validated review domain (past the marker/freshness/PR-identity
    checks in `validate_receipt`), and the worst outcome of an unwanted
    collision here is exactly the misattribution/staleness class this ADR
    already names as its threat-model boundary (`.omni/tech-debt.md`) --
    two genuinely distinct findings get merged into one consolidated row,
    never a fabricated pass or a hidden blocker. Revisit if findings text
    ever becomes untrusted/adversarial input in its own right, not just
    content behind an already-validated marker.
    """
    if text is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def fingerprint(criterion, summary):
    """Deterministic, EXCLUDES domain (AC-7)."""
    payload = {"criterion": _normalize(criterion), "summary": _normalize(summary)}
    return digest(canonical_json(payload))


# ------------------------------------------------------------- DB reads ---

def _read_task_pr_url(cx, task_id):
    if not isinstance(task_id, str) or not task_id:
        raise ReceiptDenied("task_missing")
    cx.execute("BEGIN")
    try:
        row = cx.execute("SELECT id, pr_url FROM tasks WHERE id=?",
                         (task_id,)).fetchone()
    finally:
        cx.execute("COMMIT")
    if row is None:
        raise ReceiptDenied("task_missing")
    return row["pr_url"]


def _read_marker(cx, task_id, head_sha, domain):
    """Early, advisory-only marker check (fails fast before the GitHub fetch).

    This read commits immediately and is NOT the authoritative check: a
    concurrent `review marker-mint` between this read and the eventual write
    could otherwise replace the marker underneath an in-flight validation
    (architect finding on PR #246 -- a TOCTOU). The authoritative comparison
    that actually gates persistence is re-done inside the SAME BEGIN
    IMMEDIATE transaction as the INSERT, in
    `cmd_review_receipt_record` (bin/nightshift-ctl), which is what makes it
    atomic with the write rather than a separate, racy step.
    """
    cx.execute("BEGIN")
    try:
        row = cx.execute(
            "SELECT marker FROM review_dispatch_markers "
            "WHERE task_id=? AND head_sha=? AND domain=?",
            (task_id, head_sha, domain)).fetchone()
    finally:
        cx.execute("COMMIT")
    if row is None:
        raise ReceiptDenied("marker_missing")
    return row["marker"]


# --------------------------------------------------------- main entrypoint

def validate_receipt(cx, *, task_id, head_sha, domain, comment_url,
                      gh_executable=None, fetch_comment_fn=None,
                      reviewer_logins_fn=None, time_fn=time.time):
    """Validate one receipt end to end. Raises ReceiptDenied; never writes."""
    if domain not in CANONICAL_DOMAINS:
        raise ReceiptDenied("domain_unknown")
    if not isinstance(head_sha, str) or not FULL_SHA.fullmatch(head_sha):
        raise ReceiptDenied("head_sha_invalid")

    task_pr_url = _read_task_pr_url(cx, task_id)
    task_owner, task_repo, task_number = parse_pr_url(task_pr_url)

    _c_owner, _c_repo, _c_number, comment_id = parse_comment_url(comment_url)

    fetcher = fetch_comment_fn or fetch_comment
    comment_obj = fetcher(task_owner, task_repo, comment_id,
                          gh_executable=gh_executable)

    live_owner, live_repo, live_number = _authoritative_pr(comment_obj)
    if ((live_owner.lower(), live_repo.lower(), live_number)
            != (task_owner.lower(), task_repo.lower(), task_number)):
        raise ReceiptDenied("pr_mismatch")

    # The caller's --comment-url is only a routing hint (which numeric
    # comment id to fetch); everything persisted must be re-derived from the
    # FETCHED object, never trusted from the caller's string (architect
    # finding on PR #246: a decoy owner/repo in the input URL was silently
    # persisted as the receipt's comment_url even though the fetch itself was
    # correctly scoped to the task's real repo). Confirming the fetched
    # object's own numeric id matches what was requested is defense in depth
    # against `fetch_comment` ever resolving the wrong comment.
    fetched_id = comment_obj.get("id")
    if not isinstance(fetched_id, int) or str(fetched_id) != comment_id:
        raise ReceiptDenied("comment_id_mismatch")
    canonical_comment_url = (
        f"https://github.com/{live_owner}/{live_repo}/pull/{live_number}"
        f"#issuecomment-{fetched_id}")

    # Who wrote it, before anything it says is read. The marker is a
    # per-dispatch secret only until the reviewer posts it.
    user = comment_obj.get("user")
    author = user.get("login") if isinstance(user, dict) else None
    expected = (reviewer_logins_fn or expected_reviewer_logins)(
        gh_executable=gh_executable)
    if not isinstance(author, str) or author.casefold() not in expected:
        raise ReceiptDenied("comment_author_untrusted")

    created_at = comment_obj.get("created_at") or comment_obj.get("createdAt")
    updated_at = comment_obj.get("updated_at") or comment_obj.get("updatedAt")
    if not created_at or not updated_at:
        raise ReceiptDenied("comment_unreadable")
    if created_at != updated_at:
        raise ReceiptDenied("comment_edited")

    block = _extract_block(comment_obj.get("body"))
    if block["domain"] != domain:
        raise ReceiptDenied("domain_mismatch")
    if block["task_id"] != task_id:
        raise ReceiptDenied("task_id_mismatch")
    if block["head_sha"] != head_sha:
        raise ReceiptDenied("head_sha_mismatch")
    if block["verdict"] not in VERDICTS:
        raise ReceiptDenied("verdict_invalid")

    expected_marker = _read_marker(cx, task_id, head_sha, domain)
    if block["marker"] != expected_marker:
        raise ReceiptDenied("marker_mismatch")
    if marker_consumed(cx, block["marker"], canonical_comment_url):
        raise ReceiptDenied("marker_consumed")

    findings = _validate_findings(block.get("findings"))
    report_digest = digest(canonical_json(block))

    receipt = {
        "task_id": task_id,
        "head_sha": head_sha,
        "domain": domain,
        "verdict": block["verdict"],
        "pr_url": task_pr_url,
        "comment_url": canonical_comment_url,
        "marker": block["marker"],
        "report_digest": report_digest,
        "comment_created_at": created_at,
        "validated_at": time_fn(),
    }
    return {"receipt": receipt, "findings": findings}


# --------------------------------------------------- consolidation (read) --

def parse_required_domains(text):
    """Extract the checked canonical domains from a template-shaped contract.

    Fails closed (raises ReceiptDenied) on an unrecognized label, a label
    matching more than one row, a domain appearing twice, or any canonical
    domain never appearing at all -- see AC-9 and the parsing-rule paragraph
    in docs/reviews/REVIEW-CONTRACT-TEMPLATE.md.
    """
    if not isinstance(text, str):
        raise ReceiptDenied("contract_unreadable")
    try:
        start = text.index("## Required review domains")
    except ValueError as exc:
        raise ReceiptDenied("contract_domains_section_missing") from exc
    rest = text[start:]
    next_heading = rest.find("\n## ", 1)
    section = rest if next_heading == -1 else rest[:next_heading]

    seen = {}
    for line in section.splitlines():
        row = CHECKLIST_ROW.match(line.strip())
        if not row:
            continue
        checked = row.group(1).lower() == "x"
        label = row.group(2)
        matches = [domain_id for prefix, domain_id in DOMAIN_LABEL_PREFIXES
                  if label.startswith(prefix)]
        if len(matches) != 1:
            raise ReceiptDenied("contract_domain_label_unrecognized")
        domain_id = matches[0]
        if domain_id in seen:
            raise ReceiptDenied("contract_domain_label_duplicate")
        seen[domain_id] = checked

    if set(CANONICAL_DOMAINS) - set(seen):
        raise ReceiptDenied("contract_domain_row_missing")
    return sorted(domain_id for domain_id, checked in seen.items() if checked)


def consolidate_findings(rows):
    """Pure function: rows -> deterministic consolidated findings (AC-6/AC-7).

    `rows` is an iterable of mappings with keys domain, severity, criterion,
    summary, fingerprint (as persisted). Never touches the DB or GitHub.
    """
    groups = {}
    for row in rows:
        fp = row["fingerprint"]
        entry = groups.setdefault(fp, {
            "fingerprint": fp,
            "severity": row["severity"],
            "criterion": row["criterion"],
            "summary": row["summary"],
            "domains": set(),
        })
        entry["domains"].add(row["domain"])
        if SEVERITY_RANK[row["severity"]] < SEVERITY_RANK[entry["severity"]]:
            entry["severity"] = row["severity"]
            entry["criterion"] = row["criterion"]
            entry["summary"] = row["summary"]
    consolidated = [
        {**entry, "domains": sorted(entry["domains"])}
        for entry in groups.values()
    ]
    consolidated.sort(key=lambda entry: (SEVERITY_RANK[entry["severity"]],
                                         entry["fingerprint"]))
    return consolidated
