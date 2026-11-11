#!/usr/bin/env python3
"""Heartbeat persistence decision: whether a Program heartbeat turn may
touch Git at all (issue #390, ADR pending).

Pure compute, no `control.db` access, no I/O, no network, no LLM call --
same split `nightshift_bookkeeping_release.py` / `nightshift_review_plan.py`
already use: this module decides, `bin/nightshift-ctl`'s `heartbeat-persist`
command group owns every read/write against `control.db`.

FAIL CLOSED (AC-10): `decide()` returns `should_persist_to_git=False`
whenever `durable_delta_reason_code` is `None`, is not a member of the
closed `DURABLE_DELTA_REASON_CODES` enum, or is contradicted by
`diff_is_timestamp_only=True` -- regardless of what `observation_codes` or
any accompanying prose says. There is no parameter through which a plain-
language ("this really is a real change") assertion can flip the boolean;
only a value from the closed enum can, and even that is overridden by the
timestamp-only check. `decide()` never raises for a structurally-odd input
(wrong types, missing keys) -- malformed fields are treated as their safe
default (unrecognized / absent / False), the same "must never crash on a
malformed record" discipline `beat show` already established for liveness
verdicts.

Two closed enums, both extended only by editing this file, never by a
string literal at a call site:

    OBSERVATION_CODES          -- issue #390's own Required Behavior B list,
                                   16 members. These describe what a
                                   heartbeat OBSERVED; membership alone can
                                   never grant persistence -- only a
                                   recognized `durable_delta_reason_code`
                                   can (AC-4, AC-6).
    DURABLE_DELTA_REASON_CODES -- the closed set of reasons that CAN grant
                                   persistence, each mapped to exactly one
                                   output route below.

Route mapping (informational for the caller, not part of the enforcement
check `cmd_pr_create`'s gate performs -- that gate only asks "does an
unconsumed should_persist_to_git=1 row exist for this receipt id"):

    should_persist_to_git=False              -> route "none"
    "bookkeeping_only_diff_present"           -> route "bookkeeping_fast_path"
        (the caller's NEXT call is the real, already-shipped
        `nightshift-ctl bookkeeping plan` -> `verify-head` -> `receipt-record`
        chain, issue #286/#388/PR #387 -- this module contains zero diff/path
        classification logic of its own, by design; AC-11)
    any other recognized durable-delta reason -> route "governed_normal_path"
"""

from __future__ import annotations

import hashlib
import json


# Issue #390 Required Behavior B, verbatim, one code per bullet. An
# observation code NOT in this set is still handled (never a crash) but is
# flagged via the more specific `unrecognized_observation_fail_closed`
# output reason instead of the generic `observation_only_no_delta` one --
# a diagnostic distinction only; neither can grant persistence.
OBSERVATION_CODES = frozenset({
    "queue_empty",
    "another_program_owns_work",
    "standdown",
    "no_new_dispatch",
    "dod_unchanged",
    "vendor_cooldown_unchanged",
    "issue_blocked",
    "pr_under_review",
    "merge_confirmed",
    "worker_still_running",
    "reap_found_nothing",
    "from_owner_scan_found_nothing_new",
    "heartbeat_task_still_active",
    "next_heartbeat_note_only",
    "timestamp_only_change",
    "reporting_already_on_205",
})

# The closed set of reasons that CAN grant should_persist_to_git=True.
# `bookkeeping_only_diff_present` is the one member that routes to the
# existing #286/#388 fast path instead of the normal governed path.
DURABLE_DELTA_REASON_CODES = frozenset({
    "governed_pr_ready",
    "adr_accepted_new_content",
    "bookkeeping_only_diff_present",
})

ROUTES = frozenset({"none", "bookkeeping_fast_path", "governed_normal_path"})

OUTPUT_REASON_CODES = frozenset({
    "standdown_no_authority",
    "observation_only_no_delta",
    "timestamp_only_rejected",
    "unrecognized_observation_fail_closed",
    "durable_delta_governed",
    "durable_delta_bookkeeping_only",
    "unrecognized_durable_delta_reason_fail_closed",
})

AUTHORITY_STATUSES = frozenset({"granted", "standdown"})


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _as_list(value):
    return value if isinstance(value, list) else []


def _result(should_persist, reason_code, route, outcome):
    assert reason_code in OUTPUT_REASON_CODES
    assert route in ROUTES
    return {
        "should_persist_to_git": bool(should_persist),
        "reason_code": reason_code,
        "route": route,
        "input_digest": _digest(canonical_json(outcome)),
    }


def decide(outcome):
    """The persistence-decision function (issue #390 §A).

    `outcome` is expected to carry:
      authority_status: "granted" | "standdown"  (sourced from the existing,
          unmodified #174 authority surface -- this module adds no new
          authority read and never itself calls `nightshift-ctl authority`)
      observation_codes: list[str], each ideally a member of
          OBSERVATION_CODES
      durable_delta_reason_code: str | None
      diff_is_timestamp_only: bool, default False
      diff_paths: list[str] | None (accepted, included in the digest, not
          otherwise inspected by this function -- AC-6's timestamp check is
          driven by the explicit `diff_is_timestamp_only` flag, not by this
          module re-deriving it from paths)

    Never raises. A structurally malformed field is treated as its safe
    default, never as a reason to grant persistence.
    """
    if not isinstance(outcome, dict):
        outcome = {}

    # Security review, PR #401 (MERGE_BLOCKER, whole-file swept per WA-013
    # rather than patched at the one reported site): every `x in <frozenset-
    # of-str>` membership test below requires `x` to be hashable, and JSON
    # happily produces unhashable values (lists, dicts) for ANY key. Without
    # an `isinstance(x, str)` guard BEFORE each such test, an adversarial-
    # shaped-but-still-a-dict `outcome` (e.g. `authority_status: ["x"]` or
    # `durable_delta_reason_code: ["x"]`) raises an uncaught `TypeError`
    # instead of returning the documented fail-closed result, on exactly the
    # external-input surface this module exists to make safe. Reproduced
    # against all three membership tests below before this fix (`authority_
    # status`, each element of `observation_codes`, `durable_delta_reason_
    # code`); a non-string value is now treated identically to an
    # unrecognized string at every one -- fail closed, never a crash, never
    # a reason to grant.
    authority_status = outcome.get("authority_status")
    if (not isinstance(authority_status, str)
            or authority_status not in AUTHORITY_STATUSES
            or authority_status == "standdown"):
        # Fail-safe: an unrecognized authority_status is treated exactly
        # like "standdown" -- ADR-0044 §7b step 1's own rule ("zero
        # dispatches, zero Git calls") is the only safe default here.
        return _result(False, "standdown_no_authority", "none", outcome)

    observation_codes = _as_list(outcome.get("observation_codes"))
    unrecognized_observation = any(
        not isinstance(c, str) or c not in OBSERVATION_CODES
        for c in observation_codes
    )

    durable_delta_reason_code = outcome.get("durable_delta_reason_code")
    if durable_delta_reason_code is None:
        reason = ("unrecognized_observation_fail_closed" if unrecognized_observation
                  else "observation_only_no_delta")
        return _result(False, reason, "none", outcome)

    if (not isinstance(durable_delta_reason_code, str)
            or durable_delta_reason_code not in DURABLE_DELTA_REASON_CODES):
        return _result(
            False, "unrecognized_durable_delta_reason_fail_closed", "none", outcome)

    if bool(outcome.get("diff_is_timestamp_only", False)):
        return _result(False, "timestamp_only_rejected", "none", outcome)

    if durable_delta_reason_code == "bookkeeping_only_diff_present":
        return _result(
            True, "durable_delta_bookkeeping_only", "bookkeeping_fast_path", outcome)
    return _result(True, "durable_delta_governed", "governed_normal_path", outcome)
