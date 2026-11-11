"""Bounded, read-only GitHub observations shared by Nightshift gates.

The module deliberately owns the complete check-run enumeration rule from
ADR-0020/#112.  Callers supply policy (which identities are required and what
to persist); they do not reimplement pagination or choose a same-named run.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import time
import unicodedata


MAX_GITHUB_STDOUT = 1_048_576
MAX_GITHUB_STDERR = 8_192
GITHUB_TIMEOUT_SECONDS = 20.0
MAX_GITHUB_ID = (1 << 63) - 1

#: The `author_association` values whose issues and comments may steer a lane.
#: On a public repository CONTRIBUTOR, FIRST_TIME_CONTRIBUTOR, FIRST_TIMER,
#: NONE and MANNEQUIN are strangers, and a stranger's text is data.
TRUSTED_AUTHOR_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
ASSOCIATION_RE = re.compile(r"[A-Z_]{1,32}\Z")


class GitHubUndetermined(Exception):
    """The remote answer could not be established safely."""

    reason_code = "repository_undetermined"


class CheckSetRefusal(Exception):
    """A well-formed, complete check observation deterministically refused."""

    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _terminate(process):
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=2)


def _run_bounded(argv, *, timeout=GITHUB_TIMEOUT_SECONDS,
                 stdout_limit=MAX_GITHUB_STDOUT,
                 stderr_limit=MAX_GITHUB_STDERR):
    """Execute argv without a shell while bounding time and both output pipes."""
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise GitHubUndetermined() from exc

    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    streams = {
        process.stdout.fileno(): (process.stdout, stdout, stdout_limit),
        process.stderr.fileno(): (process.stderr, stderr, stderr_limit),
    }
    for fileobj, _, _ in streams.values():
        selector.register(fileobj, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GitHubUndetermined()
            events = selector.select(remaining)
            if not events:
                raise GitHubUndetermined()
            for key, _ in events:
                fileobj, target, limit = streams[key.fd]
                try:
                    chunk = os.read(key.fd, 65_536)
                except OSError as exc:
                    raise GitHubUndetermined() from exc
                if not chunk:
                    selector.unregister(fileobj)
                    fileobj.close()
                    continue
                target.extend(chunk)
                if len(target) > limit:
                    raise GitHubUndetermined()
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired, GitHubUndetermined) as exc:
        _terminate(process)
        if isinstance(exc, GitHubUndetermined):
            raise
        raise GitHubUndetermined() from exc
    finally:
        selector.close()
        for fileobj in (process.stdout, process.stderr):
            if fileobj is not None and not fileobj.closed:
                fileobj.close()

    if returncode != 0:
        raise GitHubUndetermined()
    return bytes(stdout), bytes(stderr)


def github_get_json(endpoint, *, gh_executable=None):
    """Return one strictly decoded JSON response from a bounded GitHub GET."""
    gh = gh_executable or os.environ.get("NIGHTSHIFT_CTL_GH", "gh")
    stdout, _ = _run_bounded([gh, "api", "--method", "GET", endpoint])
    try:
        text = stdout.decode("utf-8", "strict")
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GitHubUndetermined() from exc


def _integer(value):
    return type(value) is int and 0 < value <= MAX_GITHUB_ID


def _safe_text(value, *, maximum):
    if not isinstance(value, str) or not value or len(value) > maximum:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    return not any(unicodedata.category(ch).startswith("C") for ch in value)


def check_run_sort_key(item):
    """Canonical descriptor order for one normalized check-run record."""
    return (
        item["id"], item["app_id"], item["app_slug"], item["name"],
        item["status"], item["conclusion"] or "",
    )


def enumerate_check_runs(repository, sha, *, getter=github_get_json):
    """Fetch and normalize the entire <=100 check-run set at one SHA.

    The exact query is load-bearing: GitHub's default is latest-per-name and
    would hide an older same-named failure.  `total_count == len(check_runs)`
    is the completeness proof when `per_page=100` and the total is <=100.
    """
    endpoint = (f"repos/{repository}/commits/{sha}/check-runs"
                "?filter=all&per_page=100")
    data = getter(endpoint)
    if not isinstance(data, dict):
        raise GitHubUndetermined()
    total = data.get("total_count")
    runs = data.get("check_runs")
    if type(total) is not int or total < 0 or not isinstance(runs, list):
        raise GitHubUndetermined()
    if total > 100:
        raise CheckSetRefusal("check_set_too_large")
    if total != len(runs):
        raise CheckSetRefusal("check_set_incomplete")

    normalized = []
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("app"), dict):
            raise GitHubUndetermined()
        run_id = run.get("id")
        app_id = run["app"].get("id")
        app_slug = run["app"].get("slug")
        name = run.get("name")
        status = run.get("status")
        conclusion = run.get("conclusion")
        if not _integer(run_id) or not _integer(app_id):
            raise GitHubUndetermined()
        if not _safe_text(app_slug, maximum=255):
            raise GitHubUndetermined()
        if not _safe_text(name, maximum=255):
            raise GitHubUndetermined()
        if not _safe_text(status, maximum=64):
            raise GitHubUndetermined()
        if conclusion is not None and not _safe_text(conclusion, maximum=64):
            raise GitHubUndetermined()
        normalized.append({
            "id": run_id,
            "app_id": app_id,
            "app_slug": app_slug,
            "name": name,
            "status": status,
            "conclusion": conclusion,
        })
    return sorted(normalized, key=check_run_sort_key)


#: `neutral` passes branch protection, so it is a pass. `cancelled` and
#: `stale` are neither passes nor failures -- a run superseded by a new push
#: has not broken and has not reported. Treating any of them as a failure
#: dispatches a crew at a build that did not break.
PASSING_CONCLUSIONS = frozenset({"success", "neutral"})
IGNORED_CONCLUSIONS = frozenset({"cancelled", "stale"})


def observed_check_verdict(records):
    """Judge a check set WITHOUT knowing which checks this repository requires.

    `evaluate_required_checks` below does two separable things: it asserts every
    declared identity is present, and it asserts nothing that ran failed. The
    first is a rule about THIS repository -- on a customer's repository it names
    a check that cannot exist -- which is why `release` skips the whole
    evaluation on an external repository and therefore reads no CI at all
    (#745). The second half needs no such knowledge and is what this returns.

    `skipped` is reported separately and is NOT a failure: workflows skip jobs
    for legitimate reasons. It matters because a job that fails early can skip
    the jobs that would have produced the real signal -- on PR #737 a 10-second
    ten-second lint failure skipped the three jobs that would have run the
    tests, so "one check failed" understated it to the point of being
    misleading. (Deliberately not naming those jobs: this module must not
    learn any particular project's toolchain.)
    """
    failed, skipped, incomplete, succeeded, superseded = [], [], [], [], []
    for item in records:
        name = item.get("name")
        if item.get("status") != "completed":
            incomplete.append(name)
        elif item.get("conclusion") in PASSING_CONCLUSIONS:
            succeeded.append(name)
        elif item.get("conclusion") == "skipped":
            skipped.append(name)
        elif item.get("conclusion") in IGNORED_CONCLUSIONS:
            superseded.append(name)
        else:
            failed.append(name)
    # `incomplete` is deliberately NOT adverse. A pull request read seconds
    # after it opens has every check queued, and calling that adverse dispatches
    # a crew to fix a build that has not run yet -- the "chase a failure that
    # never happened" harm this whole path exists to avoid. It is reported so a
    # caller can wait; it is not a verdict.
    adverse = bool(failed)
    return {
        "failed": failed,
        "skipped": skipped,
        # Cancelled or superseded, which is neither a pass nor a suppression.
        # Folding these into `skipped` made a run cancelled by a new push
        # announce that the build had stopped reporting.
        "superseded": superseded,
        "incomplete": incomplete,
        "succeeded": succeeded,
        "adverse": adverse,
        # No checks at all is the ABSENCE of evidence. `evaluate_required_checks`
        # refuses it outright (`check_set_empty`); this reports it, because on a
        # repository whose conventions we do not know, "no check runs" may mean
        # the project reports through commit statuses instead.
        "no_checks": not records,
        "pending": bool(incomplete),
        # Something failed AND other jobs never ran: the build did not merely
        # fail, it stopped reporting. Treating that as one red check is how a
        # run concludes it has test coverage it does not have.
        # HONEST LIMIT: this cannot tell a job suppressed BY the failure from a
        # job that never applies. A workflow with a permanently-skipped job
        # (one gated on a different event, say) makes this equal `adverse` on
        # every failure. Deciding it properly means walking the workflow's
        # `needs:` graph; until then treat it as "look closer", not as proof.
        "signal_lost": adverse and bool(skipped),
    }


def enumerate_commit_statuses(repository, sha, *, getter=github_get_json):
    """The OTHER half of a pull request's CI, which check-runs cannot see.

    GitHub has two independent reporting APIs. Actions writes check runs;
    older and third-party services -- Snyk, Semaphore, Jenkins -- write commit
    statuses. Reading only check runs makes a project whose CI reports through
    statuses look unconditionally green. PR #737 carries three Snyk statuses
    that `enumerate_check_runs` does not return, so on a customer repository
    this is not an edge case: it is half the evidence.
    """
    endpoint = f"repos/{repository}/commits/{sha}/status?per_page=100"
    data = getter(endpoint)
    if not isinstance(data, dict):
        raise GitHubUndetermined()
    contexts = data.get("statuses")
    total = data.get("total_count")
    if not isinstance(contexts, list) or type(total) is not int or total < 0:
        raise GitHubUndetermined()
    # The response carries a server-side total, so trusting the page is a
    # choice, not a necessity -- and it is the wrong one HERE above all: this
    # function exists because reading half the evidence made a pull request
    # look green. A silently truncated page does the same thing again.
    if total != len(contexts):
        raise CheckSetRefusal("status_set_incomplete")
    normalized = []
    for item in contexts:
        if not isinstance(item, dict):
            raise GitHubUndetermined()
        context = item.get("context")
        state = item.get("state")
        if not _safe_text(context, maximum=255) or not _safe_text(state, maximum=64):
            raise GitHubUndetermined()
        # A status has no "skipped": `pending` is in flight, `success` passed,
        # `failure` and `error` did not. Mapped onto the same vocabulary the
        # check-run verdict uses so one caller can judge both.
        normalized.append({
            "name": context,
            "status": "in_progress" if state == "pending" else "completed",
            "conclusion": None if state == "pending"
            else ("success" if state == "success" else "failure"),
        })
    return normalized


def enumerate_reviews(repository, number, *, getter=github_get_json):
    """Fetch and normalize the reviews on one pull request.

    Completeness is proven the same conservative way `enumerate_check_runs`
    proves it: a full page means there may be more, and a verdict on a partial
    set is worse than no verdict.
    """
    # 101 asked for, refused at 101: a full page of 100 is completely readable,
    # and refusing it would reject a pull request that can be answered. The
    # reviews endpoint returns a bare array with no server-side total, so unlike
    # `enumerate_check_runs` there is no count to compare against -- asking for
    # one more than the limit is what makes "there are more" observable.
    endpoint = f"repos/{repository}/pulls/{number}/reviews?per_page=101"
    data = getter(endpoint)
    if not isinstance(data, list):
        raise GitHubUndetermined()
    if len(data) > 100:
        raise CheckSetRefusal("review_set_too_large")

    normalized = []
    for review in data:
        if not isinstance(review, dict):
            raise GitHubUndetermined()
        user = review.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        state = review.get("state")
        submitted = review.get("submitted_at")
        if not _safe_text(login, maximum=255) or not _safe_text(state, maximum=64):
            raise GitHubUndetermined()
        if submitted is not None and not _safe_text(submitted, maximum=64):
            raise GitHubUndetermined()
        normalized.append({
            "author": login,
            "state": state,
            "submitted_at": submitted,
        })
    return normalized


def latest_review_states(reviews):
    """Each author's CURRENT position, which is not their last review.

    GitHub's own rule: a COMMENTED review leaves an author's standing verdict
    untouched, so an APPROVED followed by a comment is still an approval, and a
    CHANGES_REQUESTED followed by a comment is still blocking. Taking the last
    review of any kind reads the first case as unresolved and the second as
    resolved -- wrong in both directions.
    """
    states = {}
    for review in sorted(reviews, key=lambda r: (r.get("submitted_at") or "")):
        if review.get("state") == "COMMENTED":
            continue
        states[review.get("author")] = review.get("state")
    return states


def evaluate_required_checks(records, required_identities):
    """Require presence of every declaration and success of every run."""
    if not records:
        raise CheckSetRefusal("check_set_empty")
    observed = {(item["app_id"], item["name"]) for item in records}
    if any(identity not in observed for identity in required_identities):
        raise CheckSetRefusal("required_check_missing")
    if any(item["status"] != "completed" or item["conclusion"] != "success"
           for item in records):
        raise CheckSetRefusal("check_not_successful")
    return records


class AuthorTrustError(ValueError):
    """A trust configuration that cannot be honoured exactly as written."""


def author_trust_policy(config):
    """Who may steer a lane, from a tracker config's two optional keys.

    `trusted_author_associations` may only NARROW the default set. Widening
    is done one named login at a time through `trusted_authors`, never by
    trusting a whole class of stranger.
    """
    config = config if isinstance(config, dict) else {}
    raw = config.get("trusted_author_associations")
    if raw is None:
        associations = TRUSTED_AUTHOR_ASSOCIATIONS
    else:
        if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
            raise AuthorTrustError("trusted_author_associations must be a list of strings")
        associations = frozenset(value.upper() for value in raw)
        if not associations <= TRUSTED_AUTHOR_ASSOCIATIONS:
            raise AuthorTrustError(
                "trusted_author_associations may only narrow %s; trust an outside "
                "person by login in trusted_authors" % sorted(TRUSTED_AUTHOR_ASSOCIATIONS))
    raw = config.get("trusted_authors", [])
    if not isinstance(raw, list) or not all(
            isinstance(v, str) and LOGIN_RE.fullmatch(v) for v in raw):
        raise AuthorTrustError("trusted_authors must be a list of GitHub logins")
    return {"associations": associations,
            "logins": frozenset(value.casefold() for value in raw)}


def author_login(row):
    """The validated author login of a REST issue/comment row, or None."""
    user = row.get("user") if isinstance(row, dict) else None
    login = user.get("login") if isinstance(user, dict) else None
    return login if isinstance(login, str) and LOGIN_RE.fullmatch(login) else None


def author_association(row):
    value = row.get("author_association") if isinstance(row, dict) else None
    return value if isinstance(value, str) and ASSOCIATION_RE.fullmatch(value) else None


def author_trusted(row, policy):
    """True only for a row whose author is in the policy. Missing or malformed
    author data is untrusted, never an error the caller might skip past."""
    login = author_login(row)
    if login is not None and login.casefold() in policy["logins"]:
        return True
    return author_association(row) in policy["associations"]
