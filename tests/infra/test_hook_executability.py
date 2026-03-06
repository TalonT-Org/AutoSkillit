"""Tests for hook command executability — validates the invocation path, not just logic.

Every hook command in hooks.json must execute successfully as a subprocess,
which is how Claude Code actually runs them. This catches the ModuleNotFoundError
bug where `python3 -m autoskillit.hooks.*` fails under system Python.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

import pytest

from autoskillit.core import pkg_root


def _extract_hook_commands() -> list[str]:
    """Extract all command strings from hooks.json PreToolUse entries."""
    hooks_json = pkg_root() / "hooks" / "hooks.json"
    data = json.loads(hooks_json.read_text())
    commands: list[str] = []
    for entry in data.get("hooks", {}).get("PreToolUse", []):
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if cmd:
                commands.append(cmd)
    return commands


@pytest.mark.parametrize("command", _extract_hook_commands(), ids=_extract_hook_commands())
def test_hook_command_executable(command: str) -> None:
    """Every hook command in hooks.json must execute successfully as a subprocess."""
    # Resolve ${CLAUDE_PLUGIN_ROOT} to pkg_root() for test isolation
    resolved = command.replace("${CLAUDE_PLUGIN_ROOT}", str(pkg_root()))
    parts = shlex.split(resolved)
    # Replace python3 with sys.executable for test isolation
    if parts[0] == "python3":
        parts[0] = sys.executable
    # Run with a minimal valid event on stdin (tool_name only)
    event = json.dumps({"tool_name": "Read", "tool_input": {}})
    proc = subprocess.run(parts, input=event, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, (
        f"Hook command failed with exit code {proc.returncode}.\n"
        f"Command: {command}\n"
        f"Resolved: {' '.join(parts)}\n"
        f"stderr: {proc.stderr}"
    )
