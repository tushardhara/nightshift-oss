#!/usr/bin/env python3
"""Exact-SHA, report-only review-risk planner (ADR-0036)."""

import hashlib
import json
import os
import re
import sqlite3
import subprocess


GIT = "/usr/bin/git"
FULL_OID = re.compile(r"[0-9a-f]{40}")
RULE_KEYS = {
    "schema_version", "fast_allowlist", "ownership_rules",
    "ownership_exempt_globs", "high_risk_globs", "conditional_globs",
    "content_scan_globs", "sensitive_terms", "limits",
}
HIGH_RISK_GROUPS = {
    "constitution", "control_plane", "workflow", "schema_migration",
    "dependency", "public_contract", "security_path",
}
CONDITIONAL_GROUPS = {"qa", "ux"}
TASK_COLUMNS = ("id", "crew", "size", "spec_ref", "base_sha")
SOURCE_PATHS = (
    "bin/nightshift-ctl",
    "bin/nightshift_review_plan.py",
    "program/review-risk.json",
)


class PlanDenied(RuntimeError):
    """Fail-closed refusal which must become exit code 3."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _digest(value):
    if not isinstance(value, bytes):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _git(repo, *args, text=False):
    env = {
        "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    try:
        proc = subprocess.run(
            [GIT, "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
             "-c", "diff.external=", "-C", repo, *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlanDenied("git_unreadable") from exc
    if proc.returncode != 0:
        raise PlanDenied("git_unreadable")
    if text:
        try:
            return proc.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PlanDenied("git_unreadable") from exc
    return proc.stdout


def _exact_commit(repo, oid, reason):
    if not isinstance(oid, str) or not FULL_OID.fullmatch(oid):
        raise PlanDenied(reason)
    actual = _git(repo, "rev-parse", "--verify", f"{oid}^{{commit}}",
                  text=True).strip()
    if actual != oid:
        raise PlanDenied(reason)
    return oid


def _blob_oid(repo, commit, path):
    oid = _git(repo, "rev-parse", "--verify", f"{commit}:{path}",
               text=True).strip()
    if not FULL_OID.fullmatch(oid):
        raise PlanDenied("bundle_identity_unproved")
    return oid


def _blob(repo, oid, maximum=None):
    size_text = _git(repo, "cat-file", "-s", oid, text=True).strip()
    try:
        size = int(size_text)
    except ValueError as exc:
        raise PlanDenied("git_object_unreadable") from exc
    if size < 0 or (maximum is not None and size > maximum):
        return None
    raw = _git(repo, "cat-file", "blob", oid)
    if len(raw) != size:
        raise PlanDenied("git_object_unreadable")
    return raw


def _path(path):
    if (not isinstance(path, str) or not path or path.startswith("/")
            or "\\" in path or "\x00" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))):
        raise ValueError("invalid_path")
    return path


def _glob_regex(pattern):
    _path(pattern)
    if "?" in pattern or "[" in pattern or "]" in pattern:
        raise ValueError("invalid_glob")
    pieces = pattern.split("/")
    if any("**" in piece and piece != "**" for piece in pieces):
        raise ValueError("invalid_glob")
    result = "^"
    for index, piece in enumerate(pieces):
        if piece == "**":
            result += ".*" if index == len(pieces) - 1 else "(?:[^/]+/)*"
        else:
            result += re.escape(piece).replace(r"\*", "[^/]*")
            if index != len(pieces) - 1:
                result += "/"
    return re.compile(result + "$")


def _matches(pattern, path):
    return bool(_glob_regex(pattern).match(_path(path)))


def _string_list(value, *, nonempty=True):
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise ValueError("invalid_string_list")
    return value


def _parse_rules(raw):
    try:
        rules = json.loads(raw)
        if (not isinstance(rules, dict) or set(rules) != RULE_KEYS
                or rules["schema_version"] != 1):
            raise ValueError
        patterns = list(_string_list(rules["fast_allowlist"]))
        patterns += list(_string_list(rules["ownership_exempt_globs"],
                                      nonempty=False))
        ownership = rules["ownership_rules"]
        if not isinstance(ownership, list) or not ownership:
            raise ValueError
        for row in ownership:
            if (not isinstance(row, dict) or set(row) != {"owner", "globs"}
                    or not isinstance(row["owner"], str) or not row["owner"]):
                raise ValueError
            patterns += list(_string_list(row["globs"]))
        high = rules["high_risk_globs"]
        conditional = rules["conditional_globs"]
        if (not isinstance(high, dict) or set(high) != HIGH_RISK_GROUPS
                or not isinstance(conditional, dict)
                or set(conditional) != CONDITIONAL_GROUPS):
            raise ValueError
        for group in (high, conditional):
            for globs in group.values():
                patterns += list(_string_list(globs))
        patterns += list(_string_list(rules["content_scan_globs"]))
        for pattern in patterns:
            _glob_regex(pattern)
        terms = _string_list(rules["sensitive_terms"])
        if (len(terms) > 64 or any(term != term.lower()
                                   or len(term.encode()) > 64 for term in terms)):
            raise ValueError
        limits = rules["limits"]
        expected = {"fast_max_files", "fast_max_changed_lines",
                    "standard_max_files", "standard_max_changed_lines",
                    "max_files", "max_blob_bytes"}
        if (not isinstance(limits, dict) or set(limits) != expected
                or any(type(value) is not int or value <= 0
                       for value in limits.values())
                or limits["fast_max_files"] > limits["standard_max_files"]
                or limits["standard_max_files"] > limits["max_files"]
                or limits["fast_max_changed_lines"]
                > limits["standard_max_changed_lines"]):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PlanDenied("rules_invalid") from exc
    return rules


def _bundle_rules():
    module_path = os.path.realpath(__file__)
    root = os.path.dirname(os.path.dirname(module_path))
    top = _git(root, "rev-parse", "--show-toplevel", text=True).strip()
    if os.path.realpath(top) != root:
        raise PlanDenied("bundle_identity_unproved")
    source_sha = _git(root, "rev-parse", "--verify", "HEAD^{commit}",
                      text=True).strip()
    if not FULL_OID.fullmatch(source_sha):
        raise PlanDenied("bundle_identity_unproved")
    identities = {}
    for path in SOURCE_PATHS:
        oid = _blob_oid(root, source_sha, path)
        committed = _blob(root, oid)
        try:
            with open(os.path.join(root, path), "rb") as current:
                live = current.read()
        except OSError as exc:
            raise PlanDenied("bundle_identity_unproved") from exc
        if committed != live:
            raise PlanDenied("bundle_identity_unproved")
        identities[path] = oid
    rules_raw = _blob(root, identities["program/review-risk.json"])
    try:
        rules_text = rules_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlanDenied("rules_invalid") from exc
    rules = _parse_rules(rules_text)
    canonical = canonical_json(rules)
    return rules, {
        "bundle_sha": source_sha,
        "controller_blob_oid": identities["bin/nightshift-ctl"],
        "planner_blob_oid": identities["bin/nightshift_review_plan.py"],
        "rules_blob_oid": identities["program/review-risk.json"],
        "rules_digest": _digest(canonical),
    }


def _read_task(cx, task_id):
    if not isinstance(task_id, str) or not task_id:
        raise PlanDenied("task_missing")
    try:
        cx.execute("BEGIN")
        row = cx.execute(
            "SELECT id,crew,size,spec_ref,base_sha FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        cx.execute("COMMIT")
    except sqlite3.Error as exc:
        try:
            cx.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise PlanDenied("control_db_unreadable") from exc
    if row is None:
        raise PlanDenied("task_missing")
    return {column: row[column] for column in TASK_COLUMNS}


def _target(repo, base, head):
    repo = os.path.realpath(repo)
    if not os.path.isdir(repo):
        raise PlanDenied("target_repo_unreadable")
    top = _git(repo, "rev-parse", "--show-toplevel", text=True).strip()
    if os.path.realpath(top) != repo:
        raise PlanDenied("target_repo_unreadable")
    _exact_commit(repo, base, "base_invalid")
    _exact_commit(repo, head, "head_invalid")
    if _git_return(repo, "merge-base", "--is-ancestor", base, head) != 0:
        raise PlanDenied("head_not_descendant")
    base_tree = _git(repo, "rev-parse", f"{base}^{{tree}}", text=True).strip()
    head_tree = _git(repo, "rev-parse", f"{head}^{{tree}}", text=True).strip()
    if not FULL_OID.fullmatch(base_tree) or not FULL_OID.fullmatch(head_tree):
        raise PlanDenied("git_object_unreadable")
    return repo, base_tree, head_tree


def _git_return(repo, *args):
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    try:
        return subprocess.run(
            [GIT, "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
             "-C", repo, *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False, env=env,
        ).returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlanDenied("git_unreadable") from exc


def _changes(repo, base, head, max_files):
    raw = _git(repo, "diff", "--raw", "-z", "--no-abbrev", "--no-ext-diff",
               "--no-textconv", "-M", base, head)
    tokens = raw.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    entries, index = [], 0
    try:
        while index < len(tokens):
            header = tokens[index].decode("ascii")
            index += 1
            match = re.fullmatch(
                r":([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40}) "
                r"([0-9a-f]{40}) ([ACDMRT])(\d*)", header)
            if not match or index >= len(tokens):
                raise ValueError
            old_mode, new_mode, old_oid, new_oid, status, score = match.groups()
            first = tokens[index].decode("utf-8")
            index += 1
            if status in {"R", "C"}:
                if index >= len(tokens):
                    raise ValueError
                old_path, new_path = first, tokens[index].decode("utf-8")
                index += 1
            elif status == "A":
                old_path, new_path = None, first
            elif status == "D":
                old_path, new_path = first, None
            else:
                old_path = new_path = first
            for path in {old_path, new_path} - {None}:
                _path(path)
            entries.append({
                "status": status, "similarity": int(score) if score else None,
                "old_path": old_path, "new_path": new_path,
                "old_blob_oid": None if status == "A" else old_oid,
                "new_blob_oid": None if status == "D" else new_oid,
                "old_mode": None if status == "A" else old_mode,
                "new_mode": None if status == "D" else new_mode,
            })
            if len(entries) > max_files:
                raise PlanDenied("diff_too_large")
    except (UnicodeDecodeError, ValueError) as exc:
        raise PlanDenied("git_diff_unreadable") from exc
    entries.sort(key=lambda row: (row["new_path"] or row["old_path"],
                                  row["status"]))
    numstat = _git(repo, "diff", "--numstat", "-z", "--no-ext-diff",
                   "--no-textconv", "-M", base, head)
    records = numstat.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    changed_lines, binary, index = 0, False, 0
    while index < len(records):
        fields = records[index].split(b"\t", 2)
        index += 1
        if len(fields) != 3:
            raise PlanDenied("git_diff_unreadable")
        if fields[2] == b"":
            if index + 1 >= len(records):
                raise PlanDenied("git_diff_unreadable")
            index += 2
        if fields[0] == b"-" or fields[1] == b"-":
            binary = True
        else:
            try:
                changed_lines += int(fields[0]) + int(fields[1])
            except ValueError as exc:
                raise PlanDenied("git_diff_unreadable") from exc
    return entries, changed_lines, binary


def _owner(rules, path):
    matches = []
    for row in rules["ownership_rules"]:
        for pattern in row["globs"]:
            if _matches(pattern, path):
                score = len(pattern.replace("**", "").replace("*", ""))
                matches.append((score, row["owner"], pattern))
    if not matches:
        return None
    best = max(row[0] for row in matches)
    winners = [(owner, pattern) for score, owner, pattern in matches
               if score == best]
    return winners[0][0] if len(winners) == 1 else None


def _classify(rules, task, entries, changed_lines, binary, repo):
    profile = "FAST"
    triggers, ambiguities, extra_domains = [], [], set()
    paths = []

    def trigger(kind, **facts):
        triggers.append({"kind": kind, **facts})

    if task["crew"] not in {row["owner"] for row in rules["ownership_rules"]}:
        ambiguities.append("task_crew_missing_or_unknown")
    if task["size"] not in {"S", "M"}:
        ambiguities.append("task_size_missing_or_large")
    if not isinstance(task["spec_ref"], str) or not task["spec_ref"]:
        ambiguities.append("task_spec_ref_missing")
    if not entries:
        ambiguities.append("empty_diff")
    if binary:
        ambiguities.append("binary_numstat")
    cache = {}
    for entry in entries:
        entry_paths = [entry[f"{side}_path"] for side in ("old", "new")
                       if entry[f"{side}_path"] is not None]
        scan_entry = any(
            _matches(glob, path) for path in entry_paths
            for glob in rules["content_scan_globs"])
        for side in ("old", "new"):
            path = entry[f"{side}_path"]
            oid = entry[f"{side}_blob_oid"]
            mode = entry[f"{side}_mode"]
            if path is None:
                continue
            paths.append(path)
            high_groups = [name for name, globs in rules["high_risk_globs"].items()
                           if any(_matches(glob, path) for glob in globs)]
            for group in high_groups:
                trigger("protected_path", group=group, path=path, side=side)
                if group in {"control_plane", "workflow", "security_path"}:
                    extra_domains.add("security")
                if group in {"constitution", "control_plane", "schema_migration",
                             "dependency", "public_contract"}:
                    extra_domains.add("staff")
            for domain, globs in rules["conditional_globs"].items():
                if any(_matches(glob, path) for glob in globs):
                    extra_domains.add(domain)
            exempt = any(_matches(glob, path)
                         for glob in rules["ownership_exempt_globs"])
            owner = _owner(rules, path)
            if owner is None:
                ambiguities.append(f"owner_ambiguous:{side}:{path}")
            elif not exempt and owner != task["crew"]:
                trigger("other_owner", owner=owner, path=path, side=side)
            if mode not in {"100644", "100755"}:
                ambiguities.append(f"unsupported_mode:{side}:{path}")
            elif scan_entry:
                if oid not in cache:
                    cache[oid] = _blob(repo, oid, rules["limits"]["max_blob_bytes"])
                raw = cache[oid]
                try:
                    text = raw.decode("utf-8").lower() if raw is not None else None
                except UnicodeDecodeError:
                    text = None
                if text is None:
                    ambiguities.append(f"content_unreadable:{side}:{path}")
                else:
                    matched = sorted(term for term in rules["sensitive_terms"]
                                     if term in text)
                    if matched:
                        trigger("sensitive_content", path=path, side=side,
                                terms=matched)
                        extra_domains.add("security")
    path_set = sorted(set(paths))
    if ambiguities or triggers:
        profile = "HIGH_RISK"
    elif (task["size"] == "S"
          and len(entries) <= rules["limits"]["fast_max_files"]
          and changed_lines <= rules["limits"]["fast_max_changed_lines"]
          and all(any(_matches(glob, path) for glob in rules["fast_allowlist"])
                  for path in path_set)
          and not extra_domains):
        profile = "FAST"
    else:
        profile = "STANDARD"
    if (len(entries) > rules["limits"]["standard_max_files"]
            or changed_lines > rules["limits"]["standard_max_changed_lines"]):
        profile = "HIGH_RISK"
        trigger("blast_radius", changed_files=len(entries),
                changed_lines=changed_lines)
    domains = {"pm", "code_review"}
    if profile == "HIGH_RISK":
        domains.add("cross_crew")
    domains.update(extra_domains)
    triggers.sort(key=canonical_json)
    fast_limit = (len(entries) <= rules["limits"]["fast_max_files"]
                  and changed_lines <= rules["limits"]["fast_max_changed_lines"])
    standard_limit = (
        len(entries) <= rules["limits"]["standard_max_files"]
        and changed_lines <= rules["limits"]["standard_max_changed_lines"])
    all_fast = bool(path_set) and all(
        any(_matches(glob, path) for glob in rules["fast_allowlist"])
        for path in path_set)
    return {
        "profile": profile,
        "profile_facts": {
            "task_crew": task["crew"], "task_size": task["size"],
            "task_spec_ref": task["spec_ref"],
            "all_paths_fast_allowlisted": all_fast,
            "within_fast_limits": fast_limit,
            "within_standard_limits": standard_limit,
            "conditional_domains": sorted(extra_domains),
            "binary_numstat": binary,
        },
        "triggers": triggers,
        "ambiguities": sorted(set(ambiguities)),
        "changed_file_count": len(entries),
        "changed_lines": changed_lines,
        "changed_paths": path_set,
    }, sorted(domains)


def build_plan(cx, task_id, repo, head_sha):
    rules, authority = _bundle_rules()
    task = _read_task(cx, task_id)
    base = task["base_sha"]
    if not isinstance(base, str) or not FULL_OID.fullmatch(base):
        raise PlanDenied("base_invalid")
    repo, base_tree, head_tree = _target(repo, base, head_sha)
    entries, changed_lines, binary = _changes(
        repo, base, head_sha, rules["limits"]["max_files"])
    classification, domains = _classify(
        rules, task, entries, changed_lines, binary, repo)
    plan_body = {
        "human_review": {"mode": "parallel", "wave": 1,
                         "domains": domains},
        "gates": [
            {"gate": "deterministic", "wave": 1},
            {"gate": "exact_merge_result", "wave": 2, "after": [1]},
        ],
    }
    suggested_plan = {**plan_body,
                      "plan_digest": _digest(canonical_json(plan_body))}
    report = {
        "schema_version": 1,
        "authority": "shadow_only",
        "task": task,
        "rules": authority,
        "target": {
            "base_sha": base, "base_tree_oid": base_tree,
            "head_sha": head_sha, "head_tree_oid": head_tree,
            "changes": entries,
            "changed_manifest_digest": _digest(canonical_json(entries)),
        },
        "classification": classification,
        "suggested_domains": domains,
        "suggested_plan": suggested_plan,
    }
    report["report_digest"] = _digest(canonical_json(report))
    return report
