"""Audit C7 -- the orchestrator terminal sandbox grants no credential path and
no shared writable toolchain home.

The sandbox used to WRITE into `~/.config/gh` (the PAT, plaintext on a
headless box), `~/.cargo`, `~/go` and `~/.cache`. Project code running there
could read the token, replace `~/.cargo/bin/cargo`, or poison Go's caches,
and the next worker ran the result outside any sandbox. These checks read
the three configs that declare the terminal (`program/config.yaml` and both
crews) with the same stdlib, depth-exact reader the seat checks use.
"""

from __future__ import annotations

import os
import unittest

from test_seat_permission_mode import BUNDLE, _mapping_keys, _sequence_values

CONFIGS = (
    "config.yaml",
    os.path.join("agents", "crew_alpha", "config.yaml"),
    os.path.join("agents", "crew_beta", "config.yaml"),
)
SANDBOX = ("terminals", "shell", "os_env", "sandbox")
ENV = ("terminals", "shell", "env")
CREDENTIAL_PATHS = ("~/.config", "~/.ssh", "~/.aws", "~/.claude", "~/.codex",
                    "~/.netrc", "~/.docker", "~/.git-credentials")
TOOLCHAIN_HOMES = ("CARGO_HOME", "GOPATH", "GOMODCACHE", "GOCACHE",
                   "XDG_CACHE_HOME", "npm_config_cache")


def _under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


class TerminalSandboxGrants(unittest.TestCase):
    def _each(self):
        for relative in CONFIGS:
            path = os.path.join(BUNDLE, relative)
            mapping = {keys: value for _line, keys, value in _mapping_keys(path)}
            yield relative, path, mapping

    def test_the_terminal_is_still_sandboxed(self):
        for relative, _path, mapping in self._each():
            with self.subTest(config=relative):
                self.assertEqual(mapping.get((*SANDBOX, "type")), "auto")

    def test_only_cwd_is_writable(self):
        for relative, path, _mapping in self._each():
            with self.subTest(config=relative):
                self.assertEqual(
                    _sequence_values(path, (*SANDBOX, "write_paths")), (".",),
                    "a shared writable home path outlives the sandbox and is "
                    "executed by the next worker outside it")

    def test_no_credential_path_is_mounted_at_all(self):
        for relative, path, _mapping in self._each():
            grants = (_sequence_values(path, (*SANDBOX, "write_paths"))
                      + _sequence_values(path, (*SANDBOX, "read_paths")))
            with self.subTest(config=relative):
                leaked = [g for g in grants for c in CREDENTIAL_PATHS
                          if _under(g, c) or _under(c, g)]
                self.assertEqual(leaked, [], "credential path granted into the sandbox")
                self.assertNotIn("~", grants, "all of $HOME granted")

    def test_toolchain_homes_live_in_the_sandbox_private_tmp(self):
        for relative, _path, mapping in self._each():
            with self.subTest(config=relative):
                for name in TOOLCHAIN_HOMES:
                    value = mapping.get((*ENV, name))
                    self.assertIsNotNone(value, f"{name} unset: tools fall back to $HOME")
                    self.assertTrue(_under(value, "/tmp"), f"{name}={value}")

    def test_the_three_terminals_stay_identical(self):
        shapes = set()
        for _relative, path, mapping in self._each():
            shapes.add((
                _sequence_values(path, (*SANDBOX, "write_paths")),
                _sequence_values(path, (*SANDBOX, "read_paths")),
                tuple(sorted((k, v) for k, v in mapping.items() if k[:3] == ENV)),
            ))
        self.assertEqual(len(shapes), 1, "program and crew terminals drifted apart")


if __name__ == "__main__":
    unittest.main()
