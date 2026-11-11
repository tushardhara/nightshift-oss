"""#141 — every claude-native seat must declare a permission mode that actually
executes unattended. Nobody is awake to answer an approval prompt.

THE INCIDENT. At 19:20Z on 2026-08-24 a `platform` dispatch parked on its FIRST
`Bash` call awaiting a human approval that never comes, and was lost. The
pending elicitation read `policy_name: claude_native_permission`,
`permission_mode: "default"`, `phase: pre_tool_use`. Nothing can answer an
elicitation unattended, so a seat that hits this is a dead seat and its work is
destroyed.

WHAT THE INVESTIGATION ACTUALLY FOUND — read this before "fixing" the value.
The declaration was **not** missing and had **not** regressed. It was verified
intact at all four layers: the repo declares `permission_mode: auto` for all
seven claude-native seats and has since the first commit (14d41f8); the
registered bundle matched the repo exactly; and every one of the forty
`platform` session rows carried `terminal_launch_args
["--permission-mode","auto"]`, the parked one included. The park was an
intermittent runtime failure to APPLY a correctly-declared mode, upstream of
this repo. So this file does not change the value and must not be read as
having fixed the park.

WHY IT EXISTS ANYWAY. The standing retro finding on this project is
`declared-but-not-enforced` (cf. #124, and #129's runtime/repo divergence). The
rule "an unattended seat must not run in a mode that raises approval prompts"
was true, was believed, and was written down nowhere a machine could read — so
the first responder to this incident could not tell a missing declaration from a
present one, and burned the incident window proving the repo was already right.
This file makes that check one command.

It also guards a second, sharper hazard. The mode names are actively
misleading, so the obvious "fix" is a regression. Measured against the
installed Claude Code (2.1.241) by launching each mode and checking whether a
gated `Bash` call actually ran:

    auto                 EXECUTED    <-- what the repo declares
    bypassPermissions    EXECUTED    strictly more permissive, buys nothing
    acceptEdits          BLOCKED     auto-accepts *edits* only; Bash still parks
    dontAsk              BLOCKED     the name is a trap
    manual               BLOCKED
    default              BLOCKED

`acceptEdits` and `dontAsk` are the two names a hurried responder would reach
for, and both would have left the org exactly as dead while looking fixed.

Requirement ids:
  RT-141a  every claude-native seat declares `permission_mode`
  RT-141b  the declared value is one that demonstrably executes unattended
  RT-141c  the detector distinguishes a parking bundle from a clean one

Scope, deliberately: only `claude-native` seats are checked. `claude-sdk` seats
reach their permission stance through the SDK spawn path, not this field, and
`codex-native` seats use `yolo` — neither can raise `claude_native_permission`,
so requiring the field of them would be cargo-cult.

PARSING, AND WHY IT IS DEPTH-EXACT. stdlib only, like the rest of the suite: CI
has no pip install step (see .github/workflows/tests.yml), so PyYAML is not
available. `harness:` and `permission_mode:` both live under `executor.config:`,
indented, so a column-0 scan cannot find them.

The first version of this reader only asked "am I somewhere below a top-level
`executor:`?" and then stripped the indentation off before matching. That is
not good enough, and cross-vendor review of #142 proved it: moving platform's
declaration up two spaces, from `executor.config.permission_mode` to
`executor.permission_mode`, left this guard GREEN. But the runtime reads the
value at `executor.config.permission_mode` and NOWHERE ELSE — one level too
shallow and the declaration is INERT, the seat launches at `default`, and it
parks exactly as if the field had never been written. So the accepting version
of this guard certified a dead seat as safe: the same `declared-but-not-
enforced` failure the module was written to catch, reproduced inside the
checker itself.

The reader therefore tracks the FULL key path and accepts a value only at the
exact path `executor.config.<key>`. A `permission_mode:` anywhere else is
reported by name as misplaced-and-inert rather than as missing, because "you
forgot it" and "you put it two spaces off" are different mistakes with
different fixes — and only the second one is invisible to the person who just
made it. The body of a block scalar (`prompt: |`) is skipped wholesale, so a
`permission_mode:` discussed in prose is neither read as a declaration nor
reported as a misplaced one.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(REPO, "program")

CLAUDE_NATIVE = "claude-native"

# Modes that let a seat run a gated Bash call with nobody watching. Measured,
# not assumed — see the table in this module's docstring. Keep this list
# closed: widening it is a security decision, not a typo fix.
UNATTENDED_OK = ("auto", "bypassPermissions")

# Reported instead of `None` when a seat DID declare a mode but put it where
# the runtime never reads it. "missing" and "in the wrong place" are different
# mistakes with different fixes, and only the second one is invisible to the
# person who just made it — so it is named, not lumped in.
INERT = "<inert: declared at %s, not executor.config.permission_mode>"


# The runtime consumes the value at exactly this path and nowhere else.
CONFIG_PATH = ("executor", "config")

WATCHED = ("harness", "permission_mode")

# `key:` or `key: value` at the start of a stripped line. Deliberately strict —
# a key is a bare identifier — so a line of prose that merely ends in a colon
# ("Never ask about config:") is not mistaken for a mapping key.
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):(?:\s+(.*))?$")

# A YAML block scalar introducer, with any chomping/indentation indicator.
_BLOCK_SCALAR = re.compile(r"^[|>][+-]?[0-9]*$")


def _mapping_keys(path):
    """Yield `(lineno, key path, value)` for every mapping key in a spec.

    A hand-rolled indentation walk, because CI has no PyYAML. It carries the
    FULL path to each key rather than just "am I under executor:", which is
    what lets the caller demand an exact depth instead of hoping for one.

    The body of a block scalar (`prompt: |`) is skipped wholesale, as are
    comments and list items: none of them are mapping keys, whatever they are
    indented to resemble. Skipping is fail-closed — a declaration hidden in one
    of those is not accepted, only ignored.
    """
    stack = []           # [(indent, key)] for the enclosing mappings
    scalar_indent = None  # indent of the key introducing a block scalar
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            if scalar_indent is not None:
                # Block scalar bodies must be indented past their key.
                if indent > scalar_indent:
                    continue
                scalar_indent = None
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("-"):
                continue
            match = _KEY.match(stripped)
            if not match:
                continue
            key = match.group(1)
            value = (match.group(2) or "").split("#", 1)[0].strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, key))
            if _BLOCK_SCALAR.match(value):
                scalar_indent = indent
                continue
            yield lineno, tuple(k for _, k in stack), value.strip("'\"")


def _sequence_values(path, expected_path):
    """Values in the YAML sequence at one exact mapping path.

    The production bundle is intentionally checked without PyYAML because CI
    does not install it. Like `_mapping_keys`, this ignores block-scalar prose
    and follows indentation, so a credential path mentioned in the prompt is
    not mistaken for a sandbox grant.
    """
    stack = []
    scalar_indent = None
    values = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip())
            if scalar_indent is not None:
                if indent > scalar_indent:
                    continue
                scalar_indent = None
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("- "):
                if tuple(key for _, key in stack) == expected_path:
                    values.append(
                        stripped[2:].split("#", 1)[0].strip().strip("'\""))
                continue
            match = _KEY.match(stripped)
            if not match:
                continue
            while stack and stack[-1][0] >= indent:
                stack.pop()
            key = match.group(1)
            value = (match.group(2) or "").split("#", 1)[0].strip()
            stack.append((indent, key))
            if _BLOCK_SCALAR.match(value):
                scalar_indent = indent
    return tuple(values)


def _installed_omnigent_python():
    """Interpreter for the installed Omnigent tool, or None when absent."""
    candidates = []
    executable = shutil.which("omnigent")
    if executable:
        candidates.append(executable)
    candidates.append(os.path.join(os.path.expanduser("~"), ".local", "bin", "omnigent"))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        launcher = os.path.realpath(candidate)
        python = os.path.join(os.path.dirname(launcher), "python")
        if not os.path.isfile(python) or not os.access(python, os.X_OK):
            continue
        probe = subprocess.run(
            [python, "-c", "import omnigent, omnigent.tools.manager"],
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            return python
    probe = subprocess.run(
        [sys.executable, "-c", "import omnigent, omnigent.tools.manager"],
        text=True,
        capture_output=True,
        check=False,
    )
    return sys.executable if probe.returncode == 0 else None


def executor_fields(path):
    """`(harness, permission_mode, misplaced)` declared by a seat spec.

    `harness` and `permission_mode` are the values found at exactly
    `executor.config.<key>`, the only place the runtime looks. Either is None
    when the spec does not declare it THERE — a declaration at any other depth
    does not count, because it does not count at runtime either.

    `misplaced` is the tuple of dotted paths at which a `permission_mode` key
    was found somewhere the runtime will never read it: `executor.permission_
    mode` from a two-space indentation slip, a column-0 key, anything else.
    Such a declaration is inert and the seat still parks, so it is reported
    under its own name rather than folded into "missing".

    (A misplaced `harness` is not separately reported: it simply leaves the
    seat unclassified, which `test_bundle_is_not_empty` is the backstop for.)

    Raises rather than guessing if a key is declared twice at the real path: a
    spec that says two things is not something this checker may silently pick a
    winner from. That is how declared-but-not-enforced reproduces.
    """
    found = {}
    seen = {}
    misplaced = []
    for lineno, keypath, value in _mapping_keys(path):
        key = keypath[-1]
        if key not in WATCHED:
            continue
        if keypath[:-1] == CONFIG_PATH:
            if key in seen:
                raise AssertionError(
                    "%s:%d: `%s` declared twice under %s: (also line %d)"
                    % (path, lineno, key, ".".join(CONFIG_PATH), seen[key]))
            seen[key] = lineno
            found[key] = value
        elif key == "permission_mode":
            misplaced.append(".".join(keypath))
    return found.get("harness"), found.get("permission_mode"), tuple(misplaced)


def load_seats(root):
    """Every spec as [(repo-relative path, harness, mode, misplaced)].

    Walks the tree the way the loader does — every `config.yaml` under the
    bundle root is a registered agent, whether it sits at the root, under
    `agents/<seat>/`, or under `agents/<crew>/agents/<seat>/`. Sorted so a
    failure message reads the same on every box.
    """
    seats = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if "config.yaml" in filenames:
            path = os.path.join(dirpath, "config.yaml")
            harness, mode, misplaced = executor_fields(path)
            seats.append(
                (os.path.relpath(path, root), harness, mode, misplaced))
    seats.sort()
    return seats


def parking_seats(seats):
    """[(path, mode)] for claude-native seats that would park unattended.

    A seat parks when it declares no mode at all (Claude Code then starts in
    its built-in prompting mode), declares one that still raises approval
    prompts, or declares a fine value somewhere the runtime never reads — which
    is indistinguishable from not declaring it, and is reported as `INERT` with
    the offending path so the message says what to actually fix.

    A stray misplaced key ALONGSIDE a valid `executor.config` declaration is
    not reported: the runtime honours the real one, so the seat runs. This
    function answers "does this seat park?", not "is this spec tidy?".
    """
    bad = []
    for path, harness, mode, misplaced in seats:
        if harness != CLAUDE_NATIVE:
            continue
        if mode in UNATTENDED_OK:
            continue
        if mode is None and misplaced:
            bad.append((path, INERT % ", ".join(misplaced)))
        else:
            bad.append((path, mode))
    return bad


def _write_spec(root, relpath, harness, mode, where="executor.config"):
    """Write a synthetic seat spec, placing `permission_mode` at `where`.

    `where` is the dotted parent of the key: "executor.config" is the only
    location the runtime reads, "executor" and "root" are the two misplacements
    a human actually produces from an indentation slip.
    """
    assert where in ("executor.config", "executor", "root"), where
    path = os.path.join(root, relpath, "config.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = [
        "spec_version: 1",
        "name: %s" % os.path.basename(relpath),
        "description: synthetic seat for the permission-mode detector",
    ]
    if mode is not None and where == "root":
        body.append("permission_mode: %s" % mode)
    body += [
        "",
        "executor:",
        "  type: omnigent",
    ]
    if mode is not None and where == "executor":
        body.append("  permission_mode: %s" % mode)
    body += [
        "  config:",
        "    harness: %s" % harness,
    ]
    if mode is not None and where == "executor.config":
        body.append("    permission_mode: %s" % mode)
    # A block scalar whose body mentions the very keys the reader scans for.
    # It is prose, and must not be picked up as a declaration.
    body += [
        "",
        "prompt: |",
        "  You are a seat. Never ask about config:",
        "    harness: not-a-real-key",
        "    permission_mode: default",
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    return path


class RepoBundleTest(unittest.TestCase):
    """RT-141a / RT-141b — run against THE ACTUAL BUNDLE IN THIS REPO."""

    def setUp(self):
        self.seats = load_seats(BUNDLE)

    def test_bundle_is_not_empty(self):
        """A walk that silently found nothing would make every check vacuous."""
        self.assertTrue(self.seats, "no config.yaml found under %s" % BUNDLE)
        natives = [s for s in self.seats if s[1] == CLAUDE_NATIVE]
        self.assertTrue(
            natives,
            "no claude-native seat found — either the bundle moved or the "
            "reader stopped parsing `executor.config.harness`; both make "
            "RT-141a/b vacuous")

    def test_every_claude_native_seat_runs_unattended(self):
        """RT-141a + RT-141b.

        Watched RED by hand-editing `program/agents/platform/config.yaml` to
        `permission_mode: acceptEdits` — the plausible wrong fix — which this
        reports as a parking seat.
        """
        bad = parking_seats(self.seats)
        self.assertEqual(
            bad, [],
            "claude-native seats that would park on their first approval "
            "prompt with nobody awake to answer (#141): %s. Declare "
            "executor.config.permission_mode as one of %s — note that "
            "acceptEdits and dontAsk do NOT execute Bash unattended."
            % (bad, list(UNATTENDED_OK)))

    def test_prose_is_not_read_as_declaration(self):
        """The reader must not harvest keys out of a `prompt: |` block.

        Several real seat prompts discuss permissions in prose; if those were
        read as declarations the suite would report confident nonsense.
        """
        for path, harness, mode, _misplaced in self.seats:
            if harness is None:
                continue
            self.assertIn(
                harness,
                ("claude-native", "claude-sdk", "codex-native", "codex"),
                "%s: harness %r is not one this org deploys — the reader is "
                "probably picking up prose from a block scalar" % (path, harness))

    def test_program_has_one_wrapped_codex_seat_with_offline_bwrap_grants(self):
        """Config selects the seat; live host/runner env proves native tools off."""
        path = os.path.join(
            BUNDLE, "agents", "crew_alpha", "agents", "eng_codex", "config.yaml")
        wrapped = [seat for seat in self.seats if seat[1] == "codex"]
        self.assertEqual(
            [(seat[0], seat[1]) for seat in wrapped],
            [("agents/crew_alpha/agents/eng_codex/config.yaml", "codex")],
            "the current Program must expose exactly one wrapped Codex seat",
        )
        read_paths = _sequence_values(
            path, ("os_env", "sandbox", "read_paths"))
        write_paths = _sequence_values(
            path, ("os_env", "sandbox", "write_paths"))
        hidden = _sequence_values(
            path, ("os_env", "sandbox", "cwd_allow_hidden"))
        mapping = {
            keypath: value for _line, keypath, value in _mapping_keys(path)
        }

        self.assertEqual(mapping.get(("os_env", "type")), "caller_process")
        self.assertEqual(mapping.get(("os_env", "cwd")), ".")
        self.assertEqual(mapping.get(("os_env", "sandbox", "type")), "auto")
        self.assertEqual(
            read_paths, (".git",),
            "the protected Git directory is the only read-only mount")
        self.assertEqual(write_paths, (".",))
        self.assertEqual(
            mapping.get(("os_env", "sandbox", "allow_network")), "false")
        self.assertEqual(
            {".cache", ".npm", ".cargo"}.intersection(hidden), set(),
            "cache paths must not be separately exposed over the workspace bind",
        )
        host_grants = [
            value for value in (*read_paths, *write_paths, *hidden)
            if value.startswith("~") or os.path.isabs(value)
        ]
        self.assertEqual(
            host_grants, [],
            "auto/bwrap grants must not mount host-home or absolute paths",
        )
        self.assertEqual(
            {
                ".codex-tmp", "~/.codex-tmp", "~/.config/gh",
                "~/.gitconfig", "~/.ssh",
            }.intersection(
                set(read_paths) | set(write_paths) | set(hidden)
            ),
            set(),
            "credential-bearing or native-tool dotdirs must not be granted")
        with open(path, encoding="utf-8") as handle:
            prompt = handle.read()
        self.assertIn("Do not use structured source helpers in v1", prompt)
        self.assertIn(
            "sandboxed `sys_os_shell` for every source read, write, edit, and\n"
            "  test",
            prompt,
        )
        self.assertNotIn("WHAT THE BOX ALREADY HAS", prompt)
        self.assertNotIn("cloud code IS testable here", prompt)
        self.assertNotIn("`floci start`", prompt)
        self.assertIn(
            "Rust/cargo and Docker/floci validation require a separately "
            "reviewed platform\n  grant and must be reported unavailable",
            prompt,
        )

    def test_installed_tool_manager_enforces_worker_bwrap_boundary(self):
        """Exercise the exact installed ToolManager/OSEnv path on Linux."""
        if sys.platform != "linux":
            self.skipTest("installed-runtime bwrap proof requires Linux")
        if shutil.which("bwrap") is None:
            self.skipTest("bubblewrap is not installed")
        omnigent_python = _installed_omnigent_python()
        if omnigent_python is None:
            self.skipTest("installed Omnigent runtime is unavailable")

        spec_path = os.path.join(
            BUNDLE, "agents", "crew_alpha", "agents", "eng_codex", "config.yaml"
        )
        spec_bundle = os.path.dirname(spec_path)
        probe = textwrap.dedent(
            r"""
            import json
            import os
            import socket
            import sys
            from pathlib import Path

            from omnigent.spec import load
            from omnigent.tools.base import ToolContext
            from omnigent.tools.manager import ToolManager

            spec_path = Path(sys.argv[1])
            workspace = Path(sys.argv[2])
            os.chdir(workspace)
            spec = load(spec_path, expand_env=False)
            manager = ToolManager(
                spec, workdir=None, sandbox_enabled=True
            )
            context = ToolContext(
                task_id="sandbox-proof",
                agent_id="crew_alpha.eng_codex",
                workspace=workspace,
            )

            def call(name, arguments):
                result = manager.call_tool(
                    name, json.dumps(arguments), context
                )
                parsed = json.loads(result)
                assert isinstance(parsed, dict), (name, parsed)
                return parsed

            try:
                required = {
                    "sys_os_write", "sys_os_read", "sys_os_edit", "sys_os_shell"
                }
                assert required.issubset(manager.get_tool_names())

                written = call(
                    "sys_os_write",
                    {"path": "structured.txt", "content": "structured-write\n"},
                )
                assert "error" not in written, written

                source_read = call("sys_os_read", {"path": "structured.txt"})
                assert "error" in source_read, source_read
                source_edit = call(
                    "sys_os_edit",
                    {
                        "path": "structured.txt",
                        "oldText": "structured-write",
                        "newText": "structured-edit",
                    },
                )
                assert "error" in source_edit, source_edit

                shell_source = call(
                    "sys_os_shell",
                    {
                        "command": (
                            "test \"$(cat structured.txt)\" = structured-write && "
                            "printf 'shell-edit\\n' > structured.txt && "
                            "test \"$(cat structured.txt)\" = shell-edit"
                        )
                    },
                )
                assert shell_source.get("exit_code") == 0, shell_source

                git_read = call("sys_os_read", {"path": ".git/HEAD"})
                assert git_read.get("content") == "ref: refs/heads/main\n", git_read
                git_write = call(
                    "sys_os_write",
                    {"path": ".git/worker-write", "content": "forbidden\n"},
                )
                assert "error" in git_write, git_write
                assert "read-only" in git_write["error"].casefold(), git_write

                isolation = call(
                    "sys_os_shell",
                    {
                        "command": (
                            "test ! -e \"$HOME/.config/gh/hosts.yml\" && "
                            "test ! -e \"$HOME/.cache/nightshift-host-canary\" && "
                            "test -z \"${GITHUB_TOKEN+x}\" && "
                            "/usr/bin/python3 -c 'import socket,sys; "
                            "s=socket.socket(); s.settimeout(1); "
                            "code=s.connect_ex((\"1.1.1.1\",443)); "
                            "sys.exit(0 if code else 91)'"
                        )
                    },
                )
                assert isolation.get("exit_code") == 0, isolation
            finally:
                manager.shutdown()
            print(json.dumps({"ok": True}, sort_keys=True))
            """
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = os.path.realpath(temporary)
            workspace = os.path.join(root, "workspace")
            host_home = os.path.join(root, "host-home")
            os.makedirs(os.path.join(workspace, ".git"))
            os.makedirs(os.path.join(host_home, ".config", "gh"))
            os.makedirs(os.path.join(host_home, ".cache"))
            with open(
                os.path.join(workspace, ".git", "HEAD"), "w", encoding="utf-8"
            ) as handle:
                handle.write("ref: refs/heads/main\n")
            with open(
                os.path.join(host_home, ".config", "gh", "hosts.yml"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("credential-canary\n")
            with open(
                os.path.join(host_home, ".cache", "nightshift-host-canary"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("cache-canary\n")
            env = dict(os.environ)
            env["HOME"] = host_home
            env["GITHUB_TOKEN"] = "must-not-enter-sandbox"
            completed = subprocess.run(
                [omnigent_python, "-c", probe, spec_bundle, workspace],
                cwd=workspace,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )

            self.assertEqual(
                completed.returncode,
                0,
                "installed ToolManager sandbox proof failed:\nstdout=%r\nstderr=%r"
                % (completed.stdout, completed.stderr),
            )
            self.assertEqual(json.loads(completed.stdout.strip()), {"ok": True})
            with open(
                os.path.join(workspace, "structured.txt"), encoding="utf-8"
            ) as handle:
                self.assertEqual(handle.read(), "shell-edit\n")
            self.assertFalse(os.path.exists(os.path.join(workspace, ".git", "worker-write")))


class DetectorFiresTest(unittest.TestCase):
    """RT-141c — synthetic bundles, so the gate above can never go vacuous.

    Without this, `RepoBundleTest` goes permanently green the moment the repo
    is clean and nobody can ever watch it fail again (cf. #64/RT-25, #124).
    """

    def _bundle(self, tmp, seats):
        """`seats` are (relpath, harness, mode[, where]) — see `_write_spec`."""
        root = os.path.join(tmp, "program")
        for seat in seats:
            _write_spec(root, *seat)
        return root

    def test_clean_bundle_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._bundle(tmp, [
                ("agents/platform", "claude-native", "auto"),
                ("agents/security", "claude-native", "bypassPermissions"),
                ("agents/staff", "claude-sdk", None),
                ("agents/release", "codex-native", None),
            ])
            self.assertEqual(parking_seats(load_seats(root)), [])

    def test_missing_mode_is_caught(self):
        """The failure #141 was originally hypothesised to be."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._bundle(tmp, [
                ("agents/platform", "claude-native", None),
                ("agents/staff", "claude-sdk", None),
            ])
            self.assertEqual(
                parking_seats(load_seats(root)),
                [("agents/platform/config.yaml", None)])

    def test_plausible_wrong_fixes_are_caught(self):
        """acceptEdits / dontAsk / manual / default all still park on Bash."""
        for mode in ("acceptEdits", "dontAsk", "manual", "default", "plan"):
            with tempfile.TemporaryDirectory() as tmp:
                root = self._bundle(tmp, [
                    ("agents/platform", "claude-native", mode),
                ])
                self.assertEqual(
                    parking_seats(load_seats(root)),
                    [("agents/platform/config.yaml", mode)],
                    "%r must be reported as a parking mode" % mode)

    def test_other_harnesses_are_not_required_to_declare(self):
        """claude-sdk and codex-native reach their stance another way."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._bundle(tmp, [
                ("agents/staff", "claude-sdk", None),
                ("agents/architect", "codex-native", None),
            ])
            self.assertEqual(parking_seats(load_seats(root)), [])

    def test_mode_directly_under_executor_is_inert(self):
        """RT-141d — `executor.permission_mode` is not what the runtime reads.

        The runtime honours the value only at `executor.config.permission_mode`.
        One level too shallow and the declaration is INERT: the seat launches at
        `default` and parks on its first gated call, exactly as if the field had
        never been written. A guard that accepts it certifies a dead seat as
        safe — which is the failure class this whole module exists to prevent,
        and the slip a hurried responder is most likely to make.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._bundle(tmp, [
                ("agents/platform", "claude-native", "auto", "executor"),
                ("agents/staff", "claude-sdk", None),
            ])
            self.assertEqual(
                parking_seats(load_seats(root)),
                [("agents/platform/config.yaml",
                  INERT % "executor.permission_mode")])

    def test_mode_at_document_root_is_inert(self):
        """The other reachable misplacement: column 0, outside `executor:`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._bundle(tmp, [
                ("agents/platform", "claude-native", "auto", "root"),
            ])
            self.assertEqual(
                parking_seats(load_seats(root)),
                [("agents/platform/config.yaml", INERT % "permission_mode")])

    def test_double_declaration_raises(self):
        """A spec that says two things fails loudly, never half-read."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "program", "agents", "x", "config.yaml")
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as f:
                f.write("spec_version: 1\nname: x\n"
                        "executor:\n  config:\n"
                        "    harness: claude-native\n"
                        "    permission_mode: auto\n"
                        "    permission_mode: default\n")
            with self.assertRaises(AssertionError):
                executor_fields(path)


if __name__ == "__main__":
    unittest.main()
