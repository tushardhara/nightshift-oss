"""Tracker-bound issue-write fence -- every `gh issue`/`gh api` issue
mutation binds to Nightshift's OWN tracker repository, mechanically,
independent of the working directory.

WHY THIS EXISTS
================
`gh` resolves its target repository from the current working directory
whenever no `--repo`/`-R` is given. Every issue-write call site in this
codebase (`program/agents/*/pm*/config.yaml`, `program/skills/tracker/
SKILL.md`, `bin/nightshift-tracker-init.sh`) historically ran bare `gh issue
create`/`edit`/`comment`/`close`/`pin` with no `--repo`. A worker operating
inside a repository Nightshift does not own -- a customer checkout, opened
to work a task there -- therefore files Nightshift's own internal
FOLLOW_UP tickets (ADR-0057: one per non-blocking finding, ~3.1 per pull
request historically) into the CUSTOMER'S issue tracker.

This module is the mechanism half of the fix. `bin/nightshift-file-issue`
is the sanctioned wrapper that supplies the correct `--repo` automatically;
this fence is what makes the raw, unwrapped form UNAVAILABLE rather than
merely discouraged -- "remember to pass --repo" is prose, and prose is
exactly the class of thing #699's merge-instruction fence had to close
mechanically instead of trusting.

WHERE THE TRACKER COMES FROM
=============================
Exactly one source: `nightshift_tracker_authority.resolve_tracker`, reading
`<state_root>/authority/<repository_id>/tracker.db`, addressed by exactly
two environment variables -- `NIGHTSHIFT_STATE_ROOT` and
`NIGHTSHIFT_REPOSITORY_ID` (`nightshift_authority.scope_from_env`, the SAME
scope ADR-0085's merge fence reads). Never a prompt, never a YAML string a
worker can edit in a pull request, never `git remote`, never `os.getcwd()`,
never `.omni/tracker.json` (itself derived from `gh repo view` at whatever
directory happens to be checked out -- exactly the cwd-dependent signal
this fence exists to not be), never a literal repository name written into
this file (#439: Core carries no project-specific values).

FAILS CLOSED, NO ESCAPE HATCH -- same posture as
`omnipods_policies.external_repo_merge_fence` (ADR-0085), for the same
reason stated there: a denied issue write costs nothing real (the worker
retries through the sanctioned wrapper, or the finding waits one heartbeat);
a wrongly-ALLOWed issue write into a repository Nightshift does not own is
public and hard to retract. There is deliberately no
`..._FAIL_OPEN` variable anywhere in this module. Any exception reaching
`_evaluate` -- an unparseable command, a missing scope, a corrupt registry
-- is a DENY, never a crash and never an ALLOW.

THE ADR-0085 DEFECT THIS FENCE DOES NOT REPEAT
================================================
`external_repo_merge_fence` once matched a compiled regex against the RAW
SHELL STRING, so `gh "pr" "merge"` was never even classified as a merge
call (`tests/test_external_repo_merge_fence.py::MergeFenceRegexGaps`; it
now uses this lexer too). This fence instead LEXES the command with `shlex`
(`posix=True, punctuation_chars=True`, after `_prelex` resolves `$'...'`,
word-initial comments and newlines) into a real argument vector and walks
it looking for every `gh` invocation, however it is quoted, wherever it
sits in a compound command (`;`, `&&`, `||`, `|`, `&`, a newline, a
`$()`/backtick substitution, an `sh -c`/`eval`/`env -S` payload, `xargs`).
A command shlex cannot lex at all (an unbalanced quote) -- or a `command`
argument that is not even a string -- DENIES; it never falls through to
ALLOW. So does a `gh` call it cannot fully resolve (audit C4): one fed
arguments by `xargs`/`find -exec`, a command word only an expansion or
glob turns into `gh` (`${G:-gh}`, `/usr/bin/g[h]`), a shell reading its
program from stdin, a flag outside gh's own flag table. See
`_full_token_stream`/`_gh_call_argvs` below for the mechanics, and
`tests/test_tracker_issue_fence.py::IssueFenceArgvParsing`/
`IssueFenceLexerBypasses` for the named evasions this closes.

DISCLOSED LIMITS -- read before assuming this closes every gap
=================================================================
1. `gh`'s own top-level subcommand set is allow-listed
   (`_KNOWN_GH_SUBCOMMANDS`); any subcommand not on that list DENIES
   (`tracker_fence_unknown_gh_subcommand`). This is what closes
   `gh alias set fu 'issue create'; gh fu -t x` -- an alias indirection a
   regex on `gh issue create` would miss entirely -- but it means a future
   real `gh` subcommand not yet added to the list is denied until this
   module is updated. Scoped ONLY to commands whose first word normalizes
   to `gh`; every other shell command (`git`, any other CLI, and any `gh`
   subcommand already on the list -- `pr`, `repo`, `label`, `release`, …)
   is completely unaffected.
2. The recursion into an `sh -c '...'`/`eval "..."` PAYLOAD (positional --
   the argument that TERMINATES a run of the interpreter's own preamble
   flags ending in `-c`, or that sits right after a bare `eval`; see
   `_expand_embedded_gh_payloads`) is unconditional REGARDLESS of that
   payload's own shape, because whatever sits there really is what the
   shell runs. This recognises `-c` however many ordinary preamble flags
   precede it (`sh -e -c '...'`, `bash --norc -c '...'`, `bash -o pipefail
   -c '...'`) and however it is fused into a leading short-option cluster
   (`bash -lc '...'`, `zsh -ic '...'`) -- not only the bare, adjacent
   `sh -c '...'` form. A payload reached through a PREAMBLE FLAG this
   module does not enumerate (see `_PREAMBLE_FLAGS_WITH_VALUE`) that
   itself consumes a separate value token is the one shape that still
   falls through to the `$()`/backtick SHAPE check alone, same as any
   other token; a payload with neither that shape nor a recognised
   positional match will not be recursed into. A plain flag VALUE that
   merely CONTAINS the substring `gh issue create` (an issue TITLE, say)
   is deliberately never treated as a second invocation on shape or
   content alone -- only a real `$()`/backtick construct or a genuine
   `-c`/`eval` payload is.
3. `gh label create`, `gh project *`, and every other cwd-resolved GitHub
   mutation `bin/nightshift-tracker-init.sh` performs are OUT of this
   fence's scope, which covers issue writes only (`gh issue <write-verb>`
   and the equivalent `gh api`/`gh api graphql` shapes).
4. This classifies the COMMAND. A worker running as the uid that owns
   `state_root` can still overwrite `tracker.db` directly -- the same uid
   limit `nightshift_authority.py`'s own docstring states plainly for
   `authority.db`.
5. `_build_var_aliases` resolves `$NAME`/`${NAME}` ONLY when `NAME` was
   assigned, in plain `NAME=VALUE` form, earlier in THIS SAME command
   string (`G=gh; $G issue create`, and one level of chaining,
   `H=$G; $H issue create`). A `$NAME` referring to a variable already
   present in the calling shell's environment before this command ever
   ran (exported by an earlier, separate command) is NOT resolved --
   doing so would require executing the shell, not lexing one string.
   Such a `$NAME` as a command word followed by `issue`/`api` DENIES
   (it may be gh); a lone `$NAME` whose earlier-exported value is a
   whole `gh ...` command is the remaining gap, the same one a script
   file (limit #6) opens.
6. This fence classifies a SHELL COMMAND STRING. It cannot see through an
   arbitrary interpreter asked to run ARBITRARY CODE that itself shells
   out -- `python3 -c "import os; os.system('gh issue create ...')"` is
   ALLOWed, because parsing Python (or Ruby, or Node, or any other general
   -purpose language) as if it were a further shell command would be
   simply wrong, not merely incomplete, the way re-lexing an `sh -c`
   payload is. This is a materially different threat than the mechanical/
   prompt-drift bare-`gh`-call this fence exists to close: a worker
   willing to hand-write code specifically to route around a safety
   mechanism is the same actor limit #4 above already draws the line at
   (a uid that can overwrite `tracker.db` directly can just as well write
   and run that code) -- not one a command-argv lexer can be expected to
   resist.
7. `GH_REPO`/`GH_HOST` -- gh's own documented environment overrides for
   its target repository/host -- DENY an issue/api write unconditionally
   (`tracker_fence_env_override_forbidden`) when either is set via a
   plain `NAME=VALUE` prefix (optionally through `env`) DIRECTLY before
   the `gh` invocation in THIS SAME command string (`GH_HOST=x gh issue
   create --repo <tracker> ...`, `env GH_REPO=x gh ...`), same posture
   `bin/nightshift-file-issue` already applies to a caller-supplied
   override. This does NOT see a `GH_REPO`/`GH_HOST` exported by an
   EARLIER, separate command (`export GH_HOST=x` in one shell call, `gh
   issue create ...` in a later, unrelated one) -- the same boundary
   limit #5 above draws for `$NAME` variable resolution generally, and
   for the same reason: recognising that would require executing the
   shell across two calls, not lexing one command string.

INTEGRATION NOTE (read before assuming this fence is live)
=============================================================
This module is self-contained and importable/testable on its own (see
`tests/test_tracker_issue_fence.py`), but a Service-Policies-V0 evaluator
only actually intercepts tool calls once it is DECLARED at the bundle root
in `program/config.yaml` (`tests/test_external_repo_merge_fence.py`'s own
`ReleaseConfigAgreesWithMechanism.test_fence_declared_at_bundle_root` pins
this exact requirement for `external_repo_merge_fence`; #118 proved a
per-seat declaration never loads). This directory (`deploy/policies/`) is
already on the policy loader's `PYTHONPATH` (see
`omnipods_policies.py`'s own module docstring), so the declaration this
factory needs is:

    tracker_issue_write_fence:
      type: function
      on: [tool_call]
      function:
        path: nightshift_issue_fence.tracker_issue_write_fence
        arguments: {}

ADR-0088 IS THAT DECLARATION: `program/config.yaml` carries exactly this
block, immediately beside `external_repo_merge_fence`, and its own
`tests/test_tracker_issue_fence.py::ConfigDeclaresTheFence` pins it the
same way `ReleaseConfigAgreesWithMechanism` pins the merge fence's. The
same ADR rewrote the four bare `gh issue edit`/`gh issue close` lines the
program seat's own prompt carried (previously at that file's lines
628/631/648/658) -- `program/config.yaml` is platform single-writer per
`docs/owners.md`, and an ADR is exactly the process that row requires for
a change here; this was not done by editing outside that process.

This is not left as prose alone: `bin/nightshift-file-issue` resolves on
`PATH` for the process that actually execs shell tool calls
(`deploy/systemd/omnigent-host.service` prepends `__TEAMS_DIR__/bin`, the
same fix `nightshift-watchdog.service` already carried for `nightshift-ctl`
under #42), so the wrapper half of this fix is reachable now that the
fence declaration above is wired in -- there is no SECOND, independent
gap hiding behind the first one.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Callable, Collection, NamedTuple

_BIN_DIR = str(Path(__file__).resolve().parents[2] / "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

import nightshift_authority as _auth  # noqa: E402 -- scope_from_env only
import nightshift_tracker_authority as _tracker  # noqa: E402 -- resolve_tracker
from nightshift_project import OWNER_RE, REPOSITORY_RE  # noqa: E402


_Json = dict

_ALLOW = {"result": "ALLOW", "reason": ""}


def _deny(reason: str) -> _Json:
    return {"result": "DENY", "reason": reason}


#: Every shell tool name an omnigent harness puts on a tool_call event, as
#: `harness_plugins.py` declares them (claude-native/kimi-native report
#: `Bash`, codex `shell`, goose `developer__shell`, hermes `terminal`,
#: antigravity `run_command`, qwen `run_shell_command`). Unioned with
#: omnigent's own `SHELL_TOOLS` when it imports. A fence keyed on a narrower
#: set is silently inert on every harness it omits -- the `Bash` miss was
#: exactly that, on every claude-native seat.
_LOCAL_SHELL_TOOLS = frozenset({
    "sys_os_shell", "bash", "shell", "terminal", "developer__shell",
    "run_command", "run_shell_command",
})

#: Argument keys a shell tool carries its command string under.
_COMMAND_KEYS = ("command", "CommandLine", "commandLine", "cmd")


def shell_tool_names(extra: Collection[str] = ()) -> frozenset[str]:
    """Casefolded shell tool names. `extra` widens the set, never narrows it."""
    try:
        from omnigent.policies.builtins._shell import SHELL_TOOLS as upstream
    except Exception:  # not importable outside omnigent's interpreter
        upstream = frozenset()
    names = (*_LOCAL_SHELL_TOOLS, *upstream, *extra)
    return frozenset(n.casefold() for n in names if isinstance(n, str))


def _is_shell_tool(name: Any, tools: Collection[str]) -> bool:
    if not isinstance(name, str):
        return False
    key = name.casefold()
    if key.startswith("mcp__"):
        key = key.split("__", 2)[-1]  # mcp__<server>__<tool>
    return key in tools


def _tool_call(event: _Json, tool_names: Collection[str]) -> _Json | None:
    """The tool call's arguments when `event` is a call to a shell tool in
    `tool_names` (casefolded, see `shell_tool_names`), else None. The command
    is normalised under `command` whichever key the harness used. Shared with
    `omnipods_policies`, so every shell fence matches the same names."""
    if not isinstance(event, dict) or event.get("type") != "tool_call":
        return None
    data = event.get("data")
    if not isinstance(data, dict) or not _is_shell_tool(data.get("name"), tool_names):
        return None
    args = data.get("arguments")
    if not isinstance(args, dict):
        return {}
    for key in _COMMAND_KEYS:
        if key in args:
            return {**args, "command": args[key]}
    return args


class _Unparseable(Exception):
    """Raised internally whenever the command cannot be safely classified.
    Every raise site is caught exactly once, at the top of `_evaluate`, and
    turned into a DENY -- see the module docstring's fail-closed section."""


# ---------------------------------------------------------------------------
# Tokenising: shlex over a real argv, not a regex over the raw string.
# ---------------------------------------------------------------------------

_MAX_SUBSTITUTION_DEPTH = 4
_EDGE_STRIP_CHARS = "`$(){}"
_ASSIGNMENT_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _normalize_word(token: Any) -> str:
    """A comparison-only key for "is this token essentially the bare word
    `gh`" -- NEVER used as a replacement for the token itself. Strips one
    leading `NAME=` shell-assignment prefix, but ONLY when what immediately
    follows `=` is itself a substitution/grouping character -- i.e. only
    the glued-together shape `` x=`gh ...` `` or `x=$(gh ...)` (a real
    command substitution the shell evaluates regardless of the assignment
    around it) -- then strips any leading/trailing shell-substitution/
    grouping punctuation (backtick, `$`, `(`, `)`, `{`, `}`), and finally
    takes the basename so `/usr/bin/gh` normalizes the same as `gh`.

    The prefix is deliberately NOT stripped when the remainder is a plain
    word (`G=gh` -- an ordinary variable ASSIGNMENT, not an invocation of
    anything). Stripping it there was a real defect: it made `G=gh` compare
    equal to `gh` and get treated as an (empty, harmless-looking) `gh` call
    start in `_gh_call_argvs`, while the LATER real invocation via `$G`
    went unrecognised -- see `_build_var_aliases`/`_resolve_word` below for
    how `$G issue create` is closed instead, by tracking the assignment,
    not by misreading it as a call itself.

    Deliberately NOT applied to argument VALUES used elsewhere (a `gh api`
    endpoint like `repos/{owner}/{repo}/issues` legitimately contains `{`/
    `}` that must survive intact for the placeholder check) -- this
    function's return value is used only to decide "does a new `gh`
    invocation start here", never spliced back into an argv.
    """
    if not isinstance(token, str):
        return ""
    word = token
    m = _ASSIGNMENT_PREFIX_RE.match(word)
    if m:
        remainder = word[m.end():]
        if remainder and remainder[0] in _EDGE_STRIP_CHARS:
            word = remainder
    changed = True
    while changed and word:
        changed = False
        if word[0] in _EDGE_STRIP_CHARS:
            word = word[1:]
            changed = True
        if word and word[-1] in _EDGE_STRIP_CHARS:
            word = word[:-1]
            changed = True
    return os.path.basename(word)


_ANSI_C_SIMPLE = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
                  "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\",
                  "'": "'", '"': '"', "?": "?"}
_ANSI_C_NUMERIC = re.compile(
    r"x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})|([0-7]{1,3})|c(.)",
    re.S)


def _decode_ansi_c(body: str) -> str:
    """The value of bash's `$'...'` string -- `$'g\\x68'` is `gh`."""
    out: list[str] = []
    i = 0
    while i < len(body):
        if body[i] != "\\" or i + 1 >= len(body):
            out.append(body[i])
            i += 1
            continue
        if body[i + 1] in _ANSI_C_SIMPLE:
            out.append(_ANSI_C_SIMPLE[body[i + 1]])
            i += 2
            continue
        m = _ANSI_C_NUMERIC.match(body, i + 1)
        if not m:
            out.append(body[i:i + 2])
            i += 2
            continue
        if m.group(5) is not None:
            out.append(chr(ord(m.group(5)) & 0x1F))
        else:
            hexa = m.group(1) or m.group(2) or m.group(3)
            code = int(hexa, 16) if hexa else int(m.group(4), 8)
            if code > 0x10FFFF:
                raise _Unparseable("$'...' escape out of range")
            out.append(chr(code))
        i = m.end()
    return "".join(out)


def _prelex(cmd: str) -> str:
    """Rewrite the shell syntax shlex gets wrong into syntax it gets right.

    shlex models neither `$'...'` (it lexes `$'gh'` as the variable `$gh`),
    nor bash's rule that `#` opens a comment only at the START of a word
    (shlex dropped everything after `echo a#`, hiding `; gh issue create`),
    nor a newline as a command separator, nor backslash-newline as nothing.
    Each is resolved here, outside quotes only: `$'...'` becomes its decoded
    value in single quotes, `$"..."` becomes `"..."`, a word-initial `#`
    comment is removed, an unquoted newline becomes `;`, and the digits of
    an fd redirection (`2>`) are dropped so `gh 2>x issue ...` stays gh's
    argv. An unterminated `$'` is unparseable.
    """
    out: list[str] = []
    pending: list[tuple[str, bool, bool]] = []
    i, n = 0, len(cmd)
    quote: str | None = None
    word_start = True
    while i < n:
        ch = cmd[i]
        if quote == "'":
            out.append(ch)
            quote = None if ch == "'" else quote
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            if cmd[i + 1] != "\n":
                out.append(cmd[i:i + 2])
            i += 2
            word_start = False if quote is None else word_start
            continue
        if quote == '"':
            out.append(ch)
            quote = None if ch == '"' else quote
            i += 1
            continue
        if ch == "$" and cmd.startswith("$'", i):
            j = i + 2
            while j < n and cmd[j] != "'":
                j += 2 if cmd[j] == "\\" else 1
            if j >= n:
                raise _Unparseable("unterminated $'...' string")
            value = _decode_ansi_c(cmd[i + 2:j])
            out.append("'" + value.replace("'", "'\"'\"'") + "'")
            i, word_start = j + 1, False
            continue
        if ch == "$" and cmd.startswith('$"', i):
            i += 1
            continue
        if ch == "#" and word_start:
            end = cmd.find("\n", i)
            i = n if end < 0 else end
            continue
        if ch.isdigit() and word_start:
            j = i
            while j < n and cmd[j].isdigit():
                j += 1
            if j < n and cmd[j] in "<>":
                i = j
                continue
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = _HEREDOC_RE.match(cmd, i)
            if m:
                word = m.group(2)
                pending.append((re.sub(r"[\"'\\]", "", word), word != re.sub(
                    r"[\"'\\]", "", word), bool(m.group(1))))
                out.append(m.group(0))
                i, word_start = m.end(), False
                continue
        if ch == "\n":
            i = _heredoc_bodies(cmd, i + 1, pending, out)
            out.append(" ; ")
            word_start = True
            continue
        if ch in "'\"":
            quote = ch
        out.append(ch)
        word_start = ch.isspace() or ch in ";&|()<>"
        i += 1
    return "".join(out)


_HEREDOC_RE = re.compile(r"<<(-?)[ \t]*((?:[^\s;&|()<>'\"\\]|\\.|'[^']*'|\"[^\"]*\")+)")


def _heredoc_bodies(cmd: str, i: int, pending: list, out: list[str]) -> int:
    """Consume the bodies of the here-documents opened on the line that just
    ended, starting at `cmd[i]`. A body is data, not commands -- so it is
    dropped when its delimiter was quoted, and otherwise kept as ONE quoted
    word, where only its `$(...)`/backtick substitutions (which the shell
    does run) are looked into. Returns the index after the last body."""
    while pending:
        delim, quoted, strip_tabs = pending.pop(0)
        body: list[str] = []
        while i < len(cmd):
            end = cmd.find("\n", i)
            end = len(cmd) if end < 0 else end
            line, i = cmd[i:end], end + 1
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                break
            body.append(line)
        if not quoted and body:
            text = "\n".join(body).replace("\\", "\\\\").replace('"', '\\"')
            out.append(f' "{text}" ')
    return i


def _lex(cmd: str) -> list[str]:
    lx = shlex.shlex(_prelex(cmd), posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    lx.commenters = ""  # comments are removed by `_prelex`, at word starts only
    return list(lx)


#: Redirection operators: the operator and its target are not argv words.
_REDIRECT_TOKENS = frozenset({
    "<", ">", ">>", "<<", "<<<", "<>", ">|", ">&", "<&", "&>", "&>>",
})
_PUNCTUATION = frozenset("();<>|&")


def _is_separator(tok: Any) -> bool:
    """Any operator token that ends a simple command: `;`, `&&`, `|`, `|&`,
    `;;`, `(`, `)`, `<(` ... -- everything punctuation-only except a
    redirection."""
    return (isinstance(tok, str) and bool(tok) and set(tok) <= _PUNCTUATION
            and tok not in _REDIRECT_TOKENS)


#: Shell/interpreter words whose `-c` argument is a WHOLE further command,
#: not inert text -- `sh -c '...'`, `bash -c '...'`, etc. Checked
#: POSITIONALLY (this word starts a run of the interpreter's own PREAMBLE
#: flags that ends in `-c`), never by shape -- see
#: `_expand_embedded_gh_payloads`.
_INTERPRETER_WORDS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})

#: A run of fused single-letter preamble flags ending in `-c` -- `-c`
#: itself (zero leading letters), `-lc`, `-eic`, `-xc`. Real shells accept
#: `-c` fused with any number of PRECEDING single-letter flags in the same
#: token; this is checked on the RAW token (never `_normalize_word`, which
#: is for `gh`/interpreter WORD comparison, not flag shapes) and requires a
#: single leading `-` (a long option, `--norc`, has a second `-` and never
#: matches) and letters only, so it cannot be confused with `--rcfile` or
#: any other long flag that happens to end in the letter `c`.
_SHORT_C_CLUSTER_RE = re.compile(r"^-[A-Za-z]*c$")

#: Preamble flags that consume a SEPARATE following token as their value
#: (`bash`/`zsh` `-o OPTNAME`; `bash` `--rcfile FILE` / `--init-file FILE`)
#: -- skipped over, together with their value, so that value is never
#: mistaken for a positional script-file argument, which would end the
#: preamble scan before `-c` is ever seen (`bash -o pipefail -c '...'`).
_PREAMBLE_FLAGS_WITH_VALUE = frozenset({"-o", "--rcfile", "--init-file"})


def _expand_embedded_gh_payloads(tokens: list[str], depth: int) -> list[str]:
    """A single shlex token can itself swallow a WHOLE nested command
    verbatim -- `sh -c 'gh issue create'` (payload token `gh issue create`),
    `eval "gh issue create"`, a double-quoted `$(...)` (`"$(gh issue
    create)"` lexes to the ONE token `$(gh issue create)`), or several
    layers of the same wrapping nested inside each other (`sh -c "sh -c
    'gh issue create'"`, where the OUTER quote swallows a payload whose own
    first word is `sh`, not `gh`). Bounded to `_MAX_SUBSTITUTION_DEPTH`:
    nesting beyond that is unparseable, not silently accepted.

    A token is recursed into for exactly TWO distinct reasons, kept
    separate on purpose:

    1. POSITIONAL -- this token is the argument immediately following the
       `-c` that TERMINATES a run of preamble flags starting at one of
       `_INTERPRETER_WORDS` (`sh -c PAYLOAD`, `sh -e -c PAYLOAD`, `bash -lc
       PAYLOAD`, `bash -o pipefail -c PAYLOAD` -- see the preamble scan
       below), or immediately following a bare `eval` (`eval PAYLOAD`).
       Whatever sits there IS the command the shell actually runs, whatever
       its own shape.
    2. SHAPE -- the token, after stripping one leading `NAME=` assignment
       prefix, itself STARTS with a real substitution/grouping character
       (backtick, `$`, `(`) -- i.e. it IS a `$(...)`/backtick construct.
       Those are evaluated by the shell wherever they sit, including
       inside an ordinary flag value, so shape alone is enough.

    Earlier versions of this function ALSO recursed whenever a token's
    first word merely normalized to the literal word `gh` (or another
    interpreter word), with no positional or shape justification. That
    over-triggered: `--title "gh issue create is denied without --repo"`
    (a real title text this very fix's commit message would produce) has
    a first word that normalizes to `gh` with no `$()`/backtick anywhere
    and no `-c`/`eval` before it -- it is DATA, and re-lexing it as a
    second invocation produced a false DENY on the sanctioned path itself.
    Gating strictly on reason 1 or 2 above closes that false positive
    without reopening the real `sh -c`/`eval`/`$()` evasions reasons 1-2
    exist to close -- `tests/test_tracker_issue_fence.py::IssueFenceArgvParsing`
    covers both directions (`test_title_whose_first_word_is_gh_is_not_a_
    false_positive`, `test_eval_payload_is_still_recognised_positionally`,
    `test_command_substitution_inside_a_flag_value_still_denies`).

    A SHORT-LIVED regression lived here between the over-block fix above
    and this version: reason 1 was checked ONLY when `-c` sat literally
    the very next token after the interpreter word, so a single intervening
    flag (`sh -e -c`, `bash --norc -c`) -- or `-c` fused into a leading
    short-option cluster (`bash -lc`, `zsh -ic`, the single most common
    agent shell-tool wrapper shape) -- fell through to the SHAPE check
    alone, which does not fire on a payload with no `$()`/backtick of its
    own. `bash -lc 'gh issue create -t x'` ALLOWed as a result. The scan
    below walks the WHOLE run of preamble flags between the interpreter
    word and `-c` (stopping the moment it meets anything that is not a
    recognised preamble flag -- a positional script-file argument,
    `sh script.sh`, is exactly that, and is correctly left alone) --
    `tests/test_tracker_issue_fence.py::IssueFenceArgvParsing`'s
    `test_denied_inside_sh_dash_c_with_intervening_flags` and
    `test_denied_inside_bash_dash_lc_fused_short_form` pin the closed
    shapes; `test_denied_when_no_dash_c_positional_and_no_shape_present`
    pins the one that genuinely remains open (documented below).

    DISCLOSED LIMIT: a preamble flag this module does not recognise (an
    interpreter-specific long flag outside `_PREAMBLE_FLAGS_WITH_VALUE`
    that itself consumes a separate value token, e.g. a hypothetical
    `--future-flag VALUE`) would be misread as ending the preamble at that
    unrecognised flag rather than at its value, and the scan would stop
    without finding `-c` -- exactly like a genuine positional script
    argument does. This is a specific, named gap in an enumerated flag
    list, not the unbounded "any single intervening flag" gap this fix
    closes; `_PREAMBLE_FLAGS_WITH_VALUE` covers every such flag `sh`/
    `bash`/`zsh`/`dash`/`ksh` document.
    """
    if depth > _MAX_SUBSTITUTION_DEPTH:
        raise _Unparseable("substitution nesting exceeds bound")
    out: list[str] = []
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if not isinstance(tok, str):
            out.append(tok)
            i += 1
            continue
        word = _normalize_word(tok)
        if word == "eval":
            # eval joins ALL its words and runs the result.
            out.append(tok)
            j = i + 1
            while j < n and not _is_separator(tokens[j]):
                j += 1
            if j > i + 1:
                _splice_payload(" ".join(tokens[i + 1:j]), out, depth)
            i = j
            continue
        if word == "env":
            out.append(tok)
            i = _expand_env_split_string(tokens, i + 1, out, depth)
            continue
        if word in _COMMAND_STRING_RUNNERS:
            out.append(tok)
            i += 1
            while i < n and not _is_separator(tokens[i]):
                if any(ch.isspace() for ch in tokens[i]):
                    i = _consume_payload(tokens, i, out, depth)
                else:
                    out.append(tokens[i])
                    i += 1
            continue
        alias = _GIT_SHELL_ALIAS_RE.match(tok)
        if alias or (tok.startswith("!") and i > 0
                     and str(tokens[i - 1]).startswith("alias.")):
            out.append(tok)
            _splice_payload(alias.group(1) if alias else tok[1:], out, depth)
            i += 1
            continue
        if word in _INTERPRETER_WORDS:
            out.append(tok)
            i += 1
            # Walk the run of preamble flags looking for the one that IS
            # (or ends in, fused) `-c`. Stops -- treating this as NOT a
            # `-c` invocation at all -- the moment it meets a token that is
            # not a recognised preamble flag: that is either a positional
            # script-file argument (`sh script.sh`) or simply the end of
            # this interpreter's own flags, and in neither case does `-c`
            # apply here.
            found_c = False
            while i < n:
                ftok = tokens[i]
                if not isinstance(ftok, str) or not ftok.startswith("-") or ftok == "-":
                    break
                if ftok == "-c" or _SHORT_C_CLUSTER_RE.match(ftok):
                    out.append(ftok)
                    i += 1
                    found_c = True
                    break
                out.append(ftok)
                i += 1
                if ftok in _PREAMBLE_FLAGS_WITH_VALUE and i < n:
                    out.append(tokens[i])
                    i += 1
            if found_c and i < n:
                i = _consume_payload(tokens, i, out, depth)
            continue
        if any(ch.isspace() for ch in tok):
            m = _ASSIGNMENT_PREFIX_RE.match(tok)
            shape_word = tok[m.end():] if m else tok
            if shape_word and shape_word[0] in _EDGE_STRIP_CHARS:
                try:
                    nested = _lex(tok)
                except ValueError as exc:
                    raise _Unparseable(str(exc)) from exc
                out.extend(_expand_embedded_gh_payloads(nested, depth + 1))
                i += 1
                continue
        out.append(tok)
        # A substitution INSIDE a word (`"see $(gh issue create ...)"`, an
        # unquoted here-doc body) runs too, wherever it sits.
        if "$(" in tok or "`" in tok:
            for body in _substitution_bodies(tok):
                _splice_payload(body, out, depth)
        i += 1
    return out


def _consume_payload(tokens: list[str], i: int, out: list[str],
                      depth: int) -> int:
    """`tokens[i]` is a `-c`/`eval` PAYLOAD -- lex it as its own command and
    splice the (further-expanded) result into `out` in place of the raw
    payload token. Returns the next unconsumed index."""
    payload_tok = tokens[i]
    if isinstance(payload_tok, str):
        _splice_payload(payload_tok, out, depth)
    else:
        out.append(payload_tok)
    return i + 1


def _substitution_bodies(word: str) -> list[str]:
    """The commands inside every complete `$(...)` (balanced) and backtick
    pair in `word`. One that shlex split across words is left to the
    word-by-word walk, which already sees its `gh`."""
    bodies: list[str] = []
    i, n = 0, len(word)
    while i < n:
        if word.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += {"(": 1, ")": -1}.get(word[j], 0)
                j += 1
            if not depth:
                bodies.append(word[i + 2:j - 1])
            i = j
        elif word[i] == "`":
            j = word.find("`", i + 1)
            if j < 0:
                break
            bodies.append(word[i + 1:j])
            i = j + 1
        else:
            i += 1
    return bodies


def _splice_payload(text: str, out: list[str], depth: int) -> None:
    try:
        nested = _lex(text)
    except ValueError as exc:
        raise _Unparseable(str(exc)) from exc
    out.append(";")  # the payload is its own command, not more argv
    out.extend(_expand_embedded_gh_payloads(nested, depth + 1))
    out.append(";")


#: Commands whose arguments are themselves shell command strings: each of
#: their arguments that contains whitespace is lexed as a further command.
_COMMAND_STRING_RUNNERS = frozenset({
    "watch", "parallel", "sem", "ssh", "tmux", "screen", "script", "su",
    "runuser", "flock",
})

#: `git -c 'alias.x=!cmd' x` runs `cmd` through the shell.
_GIT_SHELL_ALIAS_RE = re.compile(r"^alias\.[^=\s]+=!(.+)$", re.S)

_ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir"})


def _expand_env_split_string(tokens: list[str], i: int, out: list[str],
                             depth: int) -> int:
    """`env -S STRING` / `--split-string=STRING` / `-iS'STRING'` split
    STRING into words and RUN it -- `env` acting as an interpreter. Walk
    env's own options from `tokens[i]`, splice any such STRING in as a
    command, and return the index of the first token after the options."""
    n = len(tokens)
    while i < n and isinstance(tokens[i], str) and tokens[i].startswith("-") \
            and tokens[i] != "-":
        tok = tokens[i]
        out.append(tok)
        i += 1
        if tok == "--":
            break
        if tok in ("-S", "--split-string"):
            return _consume_payload(tokens, i, out, depth) if i < n else i
        if tok.startswith("--split-string="):
            _splice_payload(tok[len("--split-string="):], out, depth)
            return i
        if not tok.startswith("--") and "S" in tok[1:]:
            rest = tok[tok.index("S", 1) + 1:]
            if rest:
                _splice_payload(rest, out, depth)
                return i
            return _consume_payload(tokens, i, out, depth) if i < n else i
        if tok in _ENV_VALUE_FLAGS and i < n:
            out.append(tokens[i])
            i += 1
    return i


_VAR_REF_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_FULL_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)


def _build_var_aliases(tokens: list[str]) -> dict[str, str]:
    """Track simple LOCAL `NAME=VALUE` assignments appearing earlier in
    THIS SAME command string, so a later bare `$NAME`/`${NAME}` reference
    resolves to `VALUE`'s normalized word for gh-invocation detection --
    closing `G=gh; $G issue create -t x`, a real evasion this fence
    previously ALLOWED (variable indirection, same defect class ADR-0085's
    `gh "pr" "merge"` quoting evasion was -- explicitly not one this fence
    was supposed to repeat).

    Deliberately narrow, and NOT general shell variable evaluation:
      - Only a token that IS, in its entirety, `NAME=VALUE` (a plain
        shlex word, no embedded whitespace) counts as an assignment here
        -- the `` x=`gh ...` `` / `x=$(gh ...)` substitution-glued shapes
        are handled by `_normalize_word`'s own direct stripping instead,
        not folded into this table.
      - A value that is itself a bare `$OTHER`/`${OTHER}` reference chains
        through an alias already seen earlier in the SAME left-to-right
        scan (`G=gh; H=$G` resolves `H` to `gh`), matching real shell
        assignment order -- but nothing else about later reassignment,
        subshells, or export semantics is modelled.
      - A `$NAME` that was never assigned WITHIN this command string is
        NOT resolved from the ambient environment (this module has no way
        to know what a real shell would see there) -- see the module
        docstring's disclosed limits for what this leaves open.
    """
    aliases: dict[str, str] = {}
    for tok in tokens:
        if not isinstance(tok, str):
            continue
        m = _FULL_ASSIGNMENT_RE.match(tok)
        if not m or any(ch.isspace() for ch in tok):
            continue
        name, value = m.group(1), m.group(2)
        if not value:
            continue
        ref = _VAR_REF_RE.match(value)
        if ref:
            resolved = aliases.get(ref.group(1))
            if resolved is not None:
                aliases[name] = resolved
            continue
        if value[0] in _EDGE_STRIP_CHARS:
            # A substitution-glued assignment (`x=`gh...`` / `x=$(gh...)`)
            # -- handled directly by `_normalize_word` elsewhere, not a
            # plain value worth aliasing.
            continue
        aliases[name] = _normalize_word(value)
    return aliases


def _resolve_word(token: Any, aliases: dict[str, str]) -> str:
    """Like `_normalize_word`, but a token that IS exactly a bare
    `$NAME`/`${NAME}` reference resolves through `aliases` first (see
    `_build_var_aliases`). Same comparison-only contract: never spliced
    back into an argv."""
    if isinstance(token, str):
        ref = _VAR_REF_RE.match(token)
        if ref:
            return aliases.get(ref.group(1), "")
    return _normalize_word(token)


def _full_token_stream(cmd: str) -> list[str]:
    try:
        tokens = _lex(cmd)
    except ValueError as exc:
        raise _Unparseable(str(exc)) from exc
    stream = _splice_multiword_values(_expand_embedded_gh_payloads(tokens, 0))
    _refuse_programs_read_from_input(stream)
    return stream


def _splice_multiword_values(tokens: list[str]) -> list[str]:
    """`C="gh issue create -R o/r"; $C` (or `eval "$C"`) runs the words of
    C's value. A bare `$NAME` whose value was assigned, with whitespace, in
    this same command is replaced by that value's tokens."""
    values: dict[str, str] = {}
    for tok in tokens:
        m = _FULL_ASSIGNMENT_RE.match(tok) if isinstance(tok, str) else None
        if m and any(ch.isspace() for ch in m.group(2)):
            values[m.group(1)] = m.group(2)
    if not values:
        return tokens
    out: list[str] = []
    for tok in tokens:
        ref = _VAR_REF_RE.match(tok) if isinstance(tok, str) else None
        if ref and ref.group(1) in values:
            _splice_payload(values[ref.group(1)], out, 1)
        else:
            out.append(tok)
    return out


#: Words that run the command named after their own options, mapped to those
#: options that take a separate value. `timeout` also takes a duration.
_WRAPPER_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "env": _ENV_VALUE_FLAGS,
    "command": frozenset(), "builtin": frozenset(), "nohup": frozenset(),
    "setsid": frozenset(), "chronic": frozenset(), "unbuffer": frozenset(),
    "exec": frozenset({"-a"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-i", "--input", "-o", "--output", "-e", "--error"}),
    "doas": frozenset({"-u", "-C"}),
    "sudo": frozenset({"-u", "--user", "-g", "--group", "-h", "--host", "-p",
                       "--prompt", "-C", "--close-from", "-D", "--chdir",
                       "-r", "--role", "-t", "--type", "-T",
                       "--command-timeout", "-U", "--other-user", "-a",
                       "--auth-type", "-c", "--login-class", "-R", "--chroot"}),
    "xargs": frozenset({"-a", "--arg-file", "-d", "--delimiter", "-E", "-I",
                        "-L", "-n", "--max-args", "-P", "--max-procs", "-s",
                        "--max-chars", "--process-slot-var"}),
}
_RESERVED_WORDS = frozenset({"if", "then", "else", "elif", "do", "while",
                             "until", "!", "{"})
#: Words that append arguments of their own (from stdin, a file, or a glob)
#: to the command they run, so that command's argv is never complete here.
_FEEDERS = frozenset({"xargs", "parallel", "sem"})
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})


def _command_positions(tokens: list[str]) -> set[int]:
    """Indices of the tokens a shell would run as a command word: the first
    word of each simple command, past `NAME=value` prefixes, reserved words
    and the wrappers in `_WRAPPER_VALUE_FLAGS` (whose own word also counts),
    and the command after `find -exec`. `command -v` is a lookup, not a run.
    """
    positions: set[int] = set()
    at_cmd = True
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if _is_separator(tok):
            at_cmd, i = True, i + 1
            continue
        if tok in _REDIRECT_TOKENS:
            i += 2
            continue
        if not at_cmd:
            at_cmd = tok in _FIND_EXEC
            i += 1
            continue
        if _ASSIGNMENT_PREFIX_RE.match(tok) or tok in _RESERVED_WORDS:
            i += 1
            continue
        positions.add(i)
        flags = _WRAPPER_VALUE_FLAGS.get(_normalize_word(tok))
        i += 1
        if flags is None:
            at_cmd = False
            continue
        word = _normalize_word(tok)
        while i < n and tokens[i].startswith("-") and tokens[i] != "-":
            opt = tokens[i]
            i += 1
            if opt == "--":
                break
            if word == "command" and ("v" in opt or "V" in opt):
                at_cmd = False
            if opt in flags and i < n:
                i += 1
        if word == "timeout" and i < n:
            i += 1
    return positions


def _fed(tokens: list[str], index: int) -> bool:
    """Whether the command at `index` is run by a `_FEEDERS` word or `find
    -exec` earlier in the same simple command."""
    for tok in reversed(tokens[:index]):
        if _is_separator(tok):
            return False
        if tok in _FIND_EXEC or _normalize_word(tok) in _FEEDERS:
            return True
    return False


_STDIN_PROGRAMS = frozenset({"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})


def _refuse_programs_read_from_input(tokens: list[str]) -> None:
    """A shell (or `source`) whose program is NOT on the command line --
    piped in (`printf 'gh issue create ...' | sh`), here-string, `-s`,
    `/dev/stdin`, process substitution, or appended by `xargs`/`find -exec`
    -- runs text this lexer never sees. Unparseable, so every fence denies.
    A script FILE argument stays allowed: disclosed limit #6."""
    for i in sorted(_command_positions(tokens)):
        tok = tokens[i]
        word = _normalize_word(tok)
        if word not in _INTERPRETER_WORDS and tok not in ("source", "."):
            continue
        if _fed(tokens, i):
            raise _Unparseable(f"`{word}` runs a program appended by xargs/find")
        j = i + 1
        while True:
            arg = tokens[j] if j < len(tokens) else ";"
            if (arg.startswith("<") or _is_separator(arg) or arg in _STDIN_PROGRAMS
                    or re.fullmatch(r"-[A-Za-z]*s[A-Za-z]*", arg)):
                raise _Unparseable(f"`{word}` reads its program from input")
            if arg in _REDIRECT_TOKENS:
                j += 2
                continue
            if _SHORT_C_CLUSTER_RE.match(arg) or arg in ("--version", "--help"):
                break
            if arg.startswith("-"):
                j += 2 if arg in _PREAMBLE_FLAGS_WITH_VALUE else 1
                continue
            break  # a script file


#: `gh`'s own documented environment overrides for its target repository/
#: host -- re-resolving what `--repo`/`-R` claims out from under it.
#: `bin/nightshift-file-issue` already refuses both by name
#: (`_REPO_OVERRIDE_ENV`); this fence's own argv classification did not,
#: which is an asymmetry between the two halves of this fix, not a
#: deliberate limit -- see `_env_override_before_gh`.
_REPO_OVERRIDE_ENV_NAMES = frozenset({"GH_REPO", "GH_HOST"})


def _env_override_before_gh(tokens: list[str], gh_index: int,
                             aliases: dict[str, str]) -> str | None:
    """Scan backward from `tokens[gh_index]` over a contiguous run of
    whitespace-free `NAME=VALUE` assignment tokens (optionally interleaved
    with a bare `env` word) -- exactly the real-shell shape `GH_HOST=x gh
    ...` / `env GH_REPO=x gh ...` -- looking for a `GH_REPO`/`GH_HOST`
    assignment that would apply to THIS `gh` invocation. Stops at the first
    separator token, the first token that is neither such an assignment
    nor `env`, or the start of the stream -- so `foo; GH_REPO=x gh ...`
    only sees the assignment (the separator resets the run), and `foo
    GH_REPO=x gh ...` (no separator -- `foo` is not a real shell command
    boundary here) correctly finds nothing usable beyond the assignment
    itself, since `foo` immediately breaks the contiguous run.

    Also resolves through `aliases` (see `_build_var_aliases`) for the
    SAME reason `_gh_call_argvs` resolves the `gh` word itself through
    them: `H=GH_REPO=cust/repo` assigning literally that string is not
    itself dangerous (it does not touch the real `GH_REPO` var), so this
    deliberately does NOT chase indirection through a variable VALUE that
    merely CONTAINS `NAME=VALUE` text -- only a literal assignment token
    counts, matching `_build_var_aliases`'s own posture on what is tracked.
    """
    i = gh_index - 1
    found: str | None = None
    while i >= 0:
        tok = tokens[i]
        if not isinstance(tok, str) or _is_separator(tok):
            break
        if _normalize_word(tok) == "env":
            i -= 1
            continue
        m = _FULL_ASSIGNMENT_RE.match(tok)
        if not m or any(ch.isspace() for ch in tok):
            break
        if m.group(1) in _REPO_OVERRIDE_ENV_NAMES:
            found = m.group(1)
        i -= 1
    return found


class _GhCall(NamedTuple):
    """One `gh` invocation found in a command.

    `fed`: an xargs/parallel/`find -exec` word runs it, so arguments this
    lexer never sees are appended to `argv`. `unresolved`: the command word
    is not literally `gh` but an expansion or glob that can produce it
    (`${G:-gh}`, `/usr/bin/g[h]`), or an unknown expansion followed by a gh
    subcommand (`$G issue create`). Neither can be classified as a complete
    argv; each fence denies them for the subcommands it guards.
    """
    argv: list[str]
    env_override: str | None
    fed: bool = False
    unresolved: bool = False


_GLOB_OR_EXPANSION_RE = re.compile(r"[$`*?\[]|\{[^}]*(,|\.\.)[^}]*\}")
_PARAM_DEFAULT_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?[-=+?]([^}]*)\}")
_EXPANSION_RE = re.compile(r"\$\{[^}]*\}|\$\([^)]*\)?|\$[A-Za-z_0-9@*#?$!-]*|`[^`]*`?")


def _is_dynamic(tok: Any) -> bool:
    """Whether the shell would rewrite `tok` before running it: a parameter
    or command expansion, a glob, or a brace expansion."""
    return isinstance(tok, str) and bool(_GLOB_OR_EXPANSION_RE.search(tok))


def _can_produce_gh(tok: str) -> bool:
    """Whether expanding `tok` can yield a command named `gh` by its own
    literal content: `${G:-gh}`, `g${X}h`, `g[h]`, `g?`, `{gh,x}`, `*`. An
    expansion with no default counts as empty, so `$X` alone does not."""
    word = _PARAM_DEFAULT_RE.sub(lambda m: m.group(1).rsplit("/", 1)[-1], tok)
    word = _EXPANSION_RE.sub("", word)
    for alternative in _brace_alternatives(word.rsplit("/", 1)[-1]):
        if fnmatch.fnmatchcase("gh", alternative):
            return True
    return False


def _brace_alternatives(word: str) -> list[str]:
    m = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not m:
        return [word]
    return [alt for part in m.group(1).split(",")
            for alt in _brace_alternatives(word[:m.start()] + part + word[m.end():])]


def _simple_command_tail(tokens: list[str], index: int) -> list[str]:
    tail: list[str] = []
    for tok in tokens[index + 1:]:
        if _is_separator(tok):
            break
        tail.append(tok)
    return tail


def _gh_shaped_tail(tail: list[str]) -> bool:
    try:
        offset, _ = _consume_leading_flags(tail)
    except _Unparseable:
        return False
    return offset < len(tail) and tail[offset] in _KNOWN_GH_SUBCOMMANDS


def _gh_call_argvs(tokens: list[str]) -> list[_GhCall]:
    """Slice the flattened token stream into one `_GhCall` per `gh`
    invocation.

    A slice runs from a `gh` word to the next separator token OR the next
    `gh` word, whichever comes first -- NOT only to a separator, so `gh` as
    an argument of another command (`xargs gh`, `timeout 5 gh`) still starts
    an invocation. Redirections and their targets are not argv words
    (`gh >/dev/null workflow run` runs `gh workflow run`).

    A `gh` word here also includes one resolved through a LOCAL variable
    alias (`G=gh; $G issue create` -- see `_build_var_aliases`), and, at a
    command position, a word that only an expansion turns into `gh` (see
    `_GhCall.unresolved`).

    `env_override` is `_env_override_before_gh`'s verdict for THIS
    specific invocation -- see that function and its one caller,
    `_evaluate_issue_call`/`_evaluate_api_call`, for why this is checked
    per-call rather than once for the whole command.
    """
    aliases = _build_var_aliases(tokens)
    positions = _command_positions(tokens)
    calls: list[_GhCall] = []
    current: list[str] | None = None
    meta: tuple = ()
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if _is_separator(tok):
            if current is not None:
                calls.append(_GhCall(current, *meta))
                current = None
            i += 1
            continue
        if tok in _REDIRECT_TOKENS:
            i += 2
            continue
        literal = _resolve_word(tok, aliases) == "gh"
        ref = _VAR_REF_RE.match(tok) if isinstance(tok, str) else None
        unresolved = (not literal and i in positions and _is_dynamic(tok)
                      and not (ref and ref.group(1) in aliases)
                      and (_can_produce_gh(tok)
                           or _gh_shaped_tail(_simple_command_tail(tokens, i))))
        if literal or unresolved:
            if current is not None:
                calls.append(_GhCall(current, *meta))
            meta = (_env_override_before_gh(tokens, i, aliases),
                    _fed(tokens, i), unresolved)
            current = [tok]
        elif current is not None:
            current.append(tok)
        i += 1
    if current is not None:
        calls.append(_GhCall(current, *meta))
    return calls


# ---------------------------------------------------------------------------
# `gh`'s own shape: global flags, top-level subcommands, issue verbs.
# ---------------------------------------------------------------------------

#: gh's real top-level subcommand set (stable across recent gh releases).
#: Anything NOT on this list DENIES -- see the module docstring's disclosed
#: limit #1. Kept deliberately close to gh's actual vocabulary rather than
#: to "whatever this codebase happens to call today", so a legitimate,
#: not-yet-used subcommand is not denied by omission.
_KNOWN_GH_SUBCOMMANDS = frozenset({
    "alias", "api", "attestation", "auth", "browse", "cache", "codespace",
    "completion", "config", "extension", "gist", "gpg-key", "issue",
    "label", "org", "pr", "preview", "project", "release", "repo", "root",
    "ruleset", "run", "search", "secret", "ssh-key", "status", "variable",
    "version", "workflow",
})

_ISSUE_READ_VERBS = frozenset({"list", "view", "status"})
_ISSUE_WRITE_VERBS = frozenset({
    "create", "edit", "comment", "close", "reopen", "delete", "transfer",
    "pin", "unpin", "lock", "unlock", "develop",
})

_GLOBAL_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
#: Global `gh` flags that take NO value and can appear before any
#: subcommand -- `gh --version`, `gh --help`/`gh -h`. Consumed and
#: skipped, same as the value flags above, so they do not raise
#: `_Unparseable` as an "unrecognised flag" and deny read-only,
#: non-issue traffic (`deploy/provision-ubuntu.sh` runs `gh --version`).
_GLOBAL_BOOLEAN_FLAGS = frozenset({"--version", "--help", "-h"})

_URL_SLUG_RE = re.compile(
    r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.IGNORECASE)

_ISSUE_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s]+)/(?:issues|pull)/\d+(?:[/?#].*)?$",
    re.IGNORECASE)
_ISSUE_NUMBER_RE = re.compile(r"^#?\d+$")

#: gh's own flag table for every `gh issue` write verb: (flags that take a
#: value, boolean flags). `-R`/`--repo` and `-h`/`--help` apply to all. A
#: flag not listed here makes the call unparseable -- guessing whether it
#: takes a value is exactly how `-q`/`-p` swallowed the next token before.
_ISSUE_COMMON_FLAGS = (frozenset({"-R", "--repo"}), frozenset({"-h", "--help"}))
_ISSUE_VERB_FLAGS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "create": (frozenset({"-a", "--assignee", "-b", "--body", "-F", "--body-file",
                          "-l", "--label", "-m", "--milestone", "-p", "--project",
                          "--recover", "-T", "--template", "-t", "--title"}),
               frozenset({"-e", "--editor", "-w", "--web"})),
    "edit": (frozenset({"--add-assignee", "--add-label", "--add-project", "-b",
                        "--body", "-F", "--body-file", "-m", "--milestone",
                        "--remove-assignee", "--remove-label", "--remove-project",
                        "-t", "--title"}),
             frozenset({"--remove-milestone"})),
    "comment": (frozenset({"-b", "--body", "-F", "--body-file"}),
                frozenset({"--create-if-none", "--delete-last", "--edit-last",
                           "-e", "--editor", "-w", "--web", "--yes"})),
    "close": (frozenset({"-c", "--comment", "-r", "--reason", "--duplicate-of"}),
              frozenset()),
    "reopen": (frozenset({"-c", "--comment"}), frozenset()),
    "delete": (frozenset(), frozenset({"--yes"})),
    "pin": (frozenset(), frozenset()),
    "unpin": (frozenset(), frozenset()),
    "lock": (frozenset({"-r", "--reason"}), frozenset()),
    "unlock": (frozenset(), frozenset()),
    "develop": (frozenset({"-b", "--base", "--branch-repo", "-n", "--name"}),
                frozenset({"-c", "--checkout", "-l", "--list"})),
}

#: `gh api`'s complete flag table. `-q`, `-p` and `--hostname` were missing
#: from the old partial list, so their values were read as the endpoint.
_API_VALUE_FLAGS = frozenset({
    "--cache", "-F", "--field", "-H", "--header", "--hostname", "--input",
    "-q", "--jq", "-X", "--method", "-p", "--preview", "-f", "--raw-field",
    "-t", "--template",
})
_API_BOOL_FLAGS = frozenset({
    "-i", "--include", "--paginate", "--silent", "--slurp", "--verbose",
    "-h", "--help",
})
_API_FIELD_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field"})
_READ_METHODS = frozenset({"GET", "HEAD"})
_GITHUB_HOSTS = frozenset({"github.com", "api.github.com"})

_GRAPHQL_ISSUE_MUTATIONS = re.compile(
    r"\b(createIssue|updateIssue|closeIssue|reopenIssue|deleteIssue|"
    r"pinIssue|unpinIssue|addComment|transferIssue|lockLockable|"
    r"unlockLockable)\b")


def _normalize_slug(value: Any) -> str | None:
    """`owner/name`, case-insensitive, or a `github.com` URL in any of its
    common forms -- the exact set `tests/test_tracker_issue_fence.py`'s
    `test_repo_equals_form_and_dash_R_and_url_forms_are_allowed` exercises.
    Returns `None` (never raises) for anything else."""
    if not isinstance(value, str) or not value:
        return None
    m = _URL_SLUG_RE.match(value.strip())
    if m:
        owner, name = m.group(1), m.group(2)
    else:
        text = value.strip()
        if text.count("/") != 1:
            return None
        owner, name = text.split("/", 1)
    if not OWNER_RE.fullmatch(owner) or not REPOSITORY_RE.fullmatch(name):
        return None
    return f"{owner}/{name}".lower()


def _consume_leading_flags(tokens: list[str]) -> tuple[int, str | None]:
    """Walk KNOWN `gh` value flags (`-R`/`--repo`[=]/`--hostname`[=]) from
    the FRONT of `tokens`. Returns `(index of the first non-flag token,
    --repo/-R value seen here or None)`.

    Used TWICE: once on `argv[1:]` to consume flags before the subcommand
    (`gh --repo o/n issue create`), and again on the tokens after `issue`
    to consume flags before the WRITE VERB (`gh issue --repo o/n create`).
    Both are valid `gh` grammar -- `--repo`/`-R` is a persistent flag `gh`
    (via Cobra) accepts before its subcommand chain is fully resolved, not
    only after it -- so a fence that only looked for `--repo` after the
    verb would let a correctly-positioned-but-pre-verb `--repo` (INCLUDING
    one pointed at the wrong repository) slip through unexamined. Raises
    `_Unparseable` on any OTHER flag-shaped token encountered before the
    first positional token -- silently stopping at an unrecognised flag and
    calling it "no verb here, not our concern" would be exactly the
    silent-fallback shape this fence exists to close.
    """
    i = 0
    n = len(tokens)
    repo_value = None
    while i < n:
        tok = tokens[i]
        if tok in _GLOBAL_VALUE_FLAGS:
            if i + 1 >= n:
                raise _Unparseable("gh flag missing a value")
            if tok in ("-R", "--repo"):
                repo_value = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("--repo="):
            repo_value = tok[len("--repo="):]
            i += 1
            continue
        if tok.startswith("--hostname="):
            i += 1
            continue
        if tok.startswith("-R") and len(tok) > 2:
            repo_value = tok[2:]
            i += 1
            continue
        if tok in _GLOBAL_BOOLEAN_FLAGS:
            i += 1
            continue
        if tok.startswith("-"):
            raise _Unparseable(f"unrecognised gh flag before a subcommand/verb: {tok!r}")
        break
    return i, repo_value


def _parse_flags(tokens: list[str], value_flags: Collection[str],
                 bool_flags: Collection[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Split `tokens` the way gh's flag parser (pflag) does: `--flag value`,
    `--flag=value`, `-Xvalue`, `-X=value`, bundled booleans (`-iX POST`),
    `--` ending the flags, and flags anywhere among positionals. Returns
    `([(flag, value), ...], positionals)`. A flag outside both tables is
    unparseable rather than guessed at."""
    values: list[tuple[str, str]] = []
    positionals: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        i += 1
        if tok == "--":
            positionals.extend(tokens[i:])
            break
        if tok.startswith("--"):
            name, eq, value = tok.partition("=")
            if name in bool_flags:
                continue
            if name not in value_flags:
                raise _Unparseable(f"unrecognised gh flag {name!r}")
            if not eq:
                if i >= n:
                    raise _Unparseable(f"gh flag {name} is missing a value")
                value, i = tokens[i], i + 1
            values.append((name, value))
            continue
        if tok.startswith("-") and tok != "-":
            for j in range(1, len(tok)):
                flag = "-" + tok[j]
                if flag in bool_flags:
                    continue
                if flag not in value_flags:
                    raise _Unparseable(f"unrecognised gh flag {flag!r} in {tok!r}")
                value = tok[j + 1:]
                value = value[1:] if value.startswith("=") else value
                if not value:
                    if i >= n:
                        raise _Unparseable(f"gh flag {flag} is missing a value")
                    value, i = tokens[i], i + 1
                values.append((flag, value))
                break
            continue
        positionals.append(tok)
    return values, positionals


class _ApiCall(NamedTuple):
    method: str
    endpoint: str | None
    fields: list[str]
    has_input: bool
    hostname: str | None


def _parse_gh_api(rest: list[str]) -> _ApiCall:
    """The effective request of `gh api <rest>`. gh sends GET unless `-X`
    names a method, and switches to POST when any field OR `--input` is
    given -- `--input` alone was treated as a read before."""
    values, positionals = _parse_flags(rest, _API_VALUE_FLAGS, _API_BOOL_FLAGS)
    if len(positionals) > 1:
        raise _Unparseable(f"gh api takes one endpoint, got {positionals!r}")
    method = None
    hostname = None
    fields: list[str] = []
    has_input = False
    for flag, value in values:
        if flag in ("-X", "--method"):
            method = value.strip().upper()
        elif flag in _API_FIELD_FLAGS:
            fields.append(value)
        elif flag == "--input":
            has_input = True
        elif flag == "--hostname":
            hostname = value.strip().lower()
    if method is None:
        method = "POST" if fields or has_input else "GET"
    return _ApiCall(method, positionals[0] if positionals else None, fields,
                    has_input, hostname)


def _api_host(endpoint: str) -> str | None:
    m = re.match(r"(?i)^[a-z][a-z0-9+.-]*://([^/?#]*)", endpoint)
    return m.group(1).lower() if m else None


def _api_segments(endpoint: str) -> list[str]:
    """The path segments `gh api` requests: scheme and host stripped (gh
    accepts a full URL), a leading `/` ignored (`/repos/...` is
    `repos/...`), the GHES `api/v3` prefix and any query dropped. A path
    with `.`/`..` segments or percent-escapes cannot be bound to a
    repository and is unparseable."""
    path = re.sub(r"(?i)^[a-z][a-z0-9+.-]*://[^/?#]*", "", endpoint)
    path = re.split(r"[?#]", path, 1)[0]
    segments = [s for s in path.split("/") if s]
    if [s.lower() for s in segments[:2]] == ["api", "v3"]:
        segments = segments[2:]
    if any(s in (".", "..") or "%" in s or "\\" in s for s in segments):
        raise _Unparseable(f"gh api endpoint {endpoint!r} is not a plain path")
    return segments


def _graphql_query_unresolvable(api: _ApiCall, fed: bool) -> bool:
    """A GraphQL document this fence cannot read: from `--input`, a file
    (`-F query=@q.graphql`), a shell variable, or appended by xargs."""
    if fed or api.has_input:
        return True
    for field in api.fields:
        key, _, value = field.partition("=")
        if key == "query" and (value.startswith("@") or _VAR_REF_RE.match(value)):
            return True
    return False


def _evaluate_issue_call(verb: str, rest: list[str], pre_verb_repo: str | None,
                          resolved_tracker: str | None, source: str,
                          env_override: str | None = None,
                          fed: bool = False) -> _Json:
    if verb in _ISSUE_READ_VERBS:
        return _ALLOW
    if verb not in _ISSUE_WRITE_VERBS:
        return _deny(
            f"tracker_fence_unknown_issue_verb: `gh issue {verb}` is not a "
            "recognised read or write verb -- denying rather than guessing "
            "(no override).")
    if verb == "transfer":
        # `gh issue transfer <number> <destination-repo>` moves an issue OUT of
        # the repository it currently lives in. The --repo/-R checked below is
        # only the SOURCE; the destination is a bare positional this fence left
        # entirely unread. So every transfer it would otherwise allow takes an
        # issue out of the declared tracker and lands it somewhere else --
        # exactly the invariant this fence exists to hold.
        #
        # There is no correct destination to permit either: a transfer whose
        # destination IS the tracker either has source == tracker (a no-op) or
        # a non-tracker source, which the --repo check below already denies.
        # So this is an outright refusal rather than a destination comparison;
        # binding the positional would add a branch that can never say yes.
        return _deny(
            "tracker_fence_transfer_forbidden: `gh issue transfer` moves an "
            "issue OUT of its repository, and its destination is a bare "
            "positional -- there is no destination for which this is a write "
            "onto Nightshift's own tracker. A human can move an issue by hand "
            "if it belongs elsewhere (no override).")
    if fed:
        return _deny(
            f"tracker_fence_unresolvable_gh: `gh issue {verb}` is run by "
            "xargs/find, which appends arguments this fence cannot see -- a "
            "`--repo` among them would win (no override).")
    if resolved_tracker is None:
        return _deny(
            f"tracker_unbound (source: {source}): Nightshift's own tracker "
            "repository has never been declared for this deployment -- "
            "every issue write is denied until an operator runs "
            "`nightshift_tracker_authority.py declare` (no override).")
    value_flags, bool_flags = _ISSUE_VERB_FLAGS[verb]
    values, positionals = _parse_flags(
        rest, value_flags | _ISSUE_COMMON_FLAGS[0], bool_flags | _ISSUE_COMMON_FLAGS[1])
    # A repo named after the verb wins over one before it (`pre_verb_repo`),
    # matching gh's own last-flag-wins rule.
    repo_value = pre_verb_repo
    for flag, value in values:
        if flag in ("-R", "--repo"):
            repo_value = value
    if repo_value is None:
        return _deny(
            f"tracker_fence_issue_write_off_tracker: `gh issue {verb}` has "
            f"no --repo/-R -- gh would resolve the target repository from "
            f"the working directory. Nightshift's tracker is "
            f"{resolved_tracker!r}; use `nightshift-file-issue {verb} ...` "
            "instead of raw `gh issue` (no override).")
    presented = _normalize_slug(repo_value)
    if presented != resolved_tracker:
        return _deny(
            f"tracker_fence_issue_write_off_tracker: `gh issue {verb}` "
            f"targets {repo_value!r}, not Nightshift's tracker "
            f"{resolved_tracker!r} (no override).")
    # An issue URL argument overrides --repo inside gh, so `gh issue close
    # https://github.com/<other>/issues/5 -R <tracker>` writes to <other>.
    # The same holds for --duplicate-of and develop's --branch-repo.
    targets = positionals + [v for f, v in values
                             if f in ("--duplicate-of", "--branch-repo")]
    for target in targets:
        if _ISSUE_NUMBER_RE.match(target):
            continue
        m = _ISSUE_URL_RE.match(target)
        slug = _normalize_slug(f"{m.group(1)}/{m.group(2)}" if m else target)
        if slug != resolved_tracker:
            return _deny(
                f"tracker_fence_issue_write_off_tracker: `gh issue {verb}` "
                f"names {target!r}, which gh resolves before --repo, and it "
                f"is not Nightshift's tracker {resolved_tracker!r} (no override).")
    if env_override:
        return _deny(
            f"tracker_fence_env_override_forbidden: `{env_override}` is "
            f"set for this `gh issue {verb}` invocation, which would "
            "re-resolve gh's actual target repository out from under the "
            "--repo/-R presented here, regardless of what it names -- "
            "same refusal `bin/nightshift-file-issue` already applies to "
            "a caller-supplied override (no override).")
    return _ALLOW


def _evaluate_api_call(rest: list[str], resolved_tracker: str | None,
                        source: str, env_override: str | None = None,
                        fed: bool = False, command: str = "") -> _Json:
    # The whole command is searched, not only this argv: the document may sit
    # in a variable assigned earlier in it (`Q='mutation{...}'; ... "$Q"`).
    if (any(t.lower() == "graphql" for t in rest)
            and _GRAPHQL_ISSUE_MUTATIONS.search(command or " ".join(rest))):
        return _deny(
            "tracker_fence_graphql_issue_mutation: `gh api graphql` "
            "carries an issue-mutating field (createIssue/closeIssue/"
            "addComment/...); a GraphQL node id cannot be statically "
            "bound to a repository, so this is denied unconditionally "
            "(no override).")
    api = _parse_gh_api(rest)
    endpoint = api.endpoint
    if endpoint is None:
        return _ALLOW  # `gh api` with no endpoint only prints usage

    if endpoint.lower() == "graphql":
        if _graphql_query_unresolvable(api, fed):
            return _deny(
                "tracker_fence_unresolvable_gh: the `gh api graphql` document "
                "comes from a file, stdin, a variable or xargs, so this fence "
                "cannot see whether it mutates an issue (no override).")
        return _ALLOW

    if "{owner}" in endpoint or "{repo}" in endpoint:
        return _deny(
            "tracker_fence_cwd_placeholder_endpoint: `gh api` endpoint "
            f"{endpoint!r} uses the {{owner}}/{{repo}} placeholder, which "
            "gh fills in from the working directory or -R -- denied "
            "unconditionally rather than trusted (no override).")

    if not fed and api.method in _READ_METHODS:
        return _ALLOW  # an explicit GET/HEAD, or no method and no fields
    segments = _api_segments(endpoint)
    path = re.split(r"[?#]", endpoint, 1)[0]
    if "$" in endpoint or "`" in endpoint or _is_dynamic(path) or (fed and "{" in path):
        return _deny(
            f"tracker_fence_unresolvable_gh: `gh api` write to {endpoint!r}, "
            "which the shell rewrites before gh sees it -- it may be an "
            "issue endpoint (no override).")
    if not any(s.lower() == "issues" for s in segments):
        return _ALLOW
    if resolved_tracker is None:
        return _deny(
            f"tracker_unbound (source: {source}): Nightshift's own tracker "
            "repository has never been declared for this deployment -- "
            "every issue write is denied (no override).")
    presented = None
    if len(segments) >= 3 and segments[0].lower() == "repos":
        presented = _normalize_slug(f"{segments[1]}/{segments[2]}")
    host = _api_host(endpoint) or api.hostname
    if presented != resolved_tracker or (host and host not in _GITHUB_HOSTS):
        return _deny(
            f"tracker_fence_issue_write_off_tracker: `gh api` writes to "
            f"{endpoint!r}, not Nightshift's tracker {resolved_tracker!r} "
            "(no override).")
    if env_override:
        return _deny(
            f"tracker_fence_env_override_forbidden: `{env_override}` is "
            "set for this `gh api` invocation, which would re-resolve "
            "gh's actual target repository out from under the endpoint "
            "presented here, regardless of what it names (no override).")
    return _ALLOW


def _evaluate_gh_argv(call: _GhCall, resolved_tracker: str | None,
                       source: str, command: str = "") -> _Json:
    argv = call.argv
    sub_index, global_repo = _consume_leading_flags(argv[1:])
    sub_index += 1  # back into argv's own indexing (argv[0] is the `gh` word)
    if sub_index >= len(argv):
        if call.fed or call.unresolved:
            return _deny(
                "tracker_fence_unresolvable_gh: `gh` takes its subcommand from "
                "xargs/find input or from an expansion of its own name -- "
                "denying a call this fence cannot read (no override).")
        return _ALLOW  # `gh` with nothing after global flags -- prints help
    subcommand = argv[sub_index]
    if subcommand not in _KNOWN_GH_SUBCOMMANDS:
        return _deny(
            f"tracker_fence_unknown_gh_subcommand: {subcommand!r} is not a "
            "recognised `gh` subcommand -- denying rather than risking an "
            "alias indirection around the issue-write fence (no override).")
    if call.unresolved and subcommand in ("issue", "api"):
        return _deny(
            f"tracker_fence_unresolvable_gh: {argv[0]!r} is not literally `gh` "
            f"but the shell can make it gh, followed by `{subcommand}` -- "
            "call gh by name (no override).")
    rest = argv[sub_index + 1:]
    if subcommand == "issue":
        # `--repo`/`-R` may legitimately sit BETWEEN `issue` and the write
        # verb too (`gh issue --repo o/n create ...` is valid gh grammar) --
        # walk past it the same way, rather than treating any leading flag
        # here as "no verb, not our concern".
        verb_index, pre_verb_repo = _consume_leading_flags(rest)
        if verb_index >= len(rest):
            if call.fed:
                return _deny(
                    "tracker_fence_unresolvable_gh: `gh issue` takes its verb "
                    "from xargs/find input (no override).")
            return _ALLOW  # `gh issue` with no verb -- gh itself will refuse this
        pre_verb_repo = pre_verb_repo if pre_verb_repo is not None else global_repo
        return _evaluate_issue_call(rest[verb_index], rest[verb_index + 1:],
                                     pre_verb_repo, resolved_tracker, source,
                                     call.env_override, call.fed)
    if subcommand == "api":
        return _evaluate_api_call(rest, resolved_tracker, source,
                                  call.env_override, call.fed, command)
    return _ALLOW


# ---------------------------------------------------------------------------
# The factory
# ---------------------------------------------------------------------------

def tracker_issue_write_fence(
    *,
    shell_tools: Collection[str] = (),
) -> Callable[[_Json], _Json]:
    """DENY every `gh issue <write-verb>` (and the equivalent `gh api`/`gh
    api graphql` issue-mutation shapes) whose target repository is not
    Nightshift's own declared tracker. See the module docstring for the
    full rationale, the resolution source, and the disclosed limits.
    `shell_tools` adds names to `shell_tool_names()`; it cannot remove any.
    """
    tools = shell_tool_names(shell_tools)

    def _evaluate(event: _Json) -> _Json:
        call = _tool_call(event, tools)
        if call is None:
            return _ALLOW
        cmd = call.get("command")
        if not isinstance(cmd, str):
            # Deliberately DENY, not ALLOW -- unlike
            # `external_repo_merge_fence`'s non-str handling. A `command`
            # argument that is not a string cannot be classified at all, so
            # "unparseable" applies just as much here as it does to a
            # shlex ValueError below; falling through to ALLOW for it would
            # be exactly the silent-allow shape this fence exists to close.
            return _deny(
                "tracker_fence_unparseable_command (command argument is "
                "not a string) -- denying rather than executing a command "
                "this fence cannot classify (no override).")
        try:
            tokens = _full_token_stream(cmd)
        except _Unparseable as exc:
            return _deny(
                f"tracker_fence_unparseable_command ({exc}) -- denying "
                "rather than executing a command this fence cannot "
                "classify (no override).")

        calls = _gh_call_argvs(tokens)
        if not calls:
            return _ALLOW

        try:
            state_root, repository_id = _auth.scope_from_env()
            resolved = _tracker.resolve_tracker(state_root, repository_id)
        except Exception as exc:  # fail closed -- see module docstring
            resolved = {"tracker": None,
                        "source": f"resolution_error:{exc.__class__.__name__}"}
        resolved_tracker = resolved.get("tracker")
        source = resolved.get("source")

        for call in calls:
            try:
                verdict = _evaluate_gh_argv(call, resolved_tracker, source, cmd)
            except _Unparseable as exc:
                return _deny(
                    f"tracker_fence_unparseable_command ({exc}) -- denying "
                    "rather than executing a command this fence cannot "
                    "classify (no override).")
            if verdict["result"] == "DENY":
                return verdict
        return _ALLOW

    return _evaluate
