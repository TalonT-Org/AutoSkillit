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
from autoskillit.hooks import HOOK_REGISTRY, generate_hooks_json


def _extract_hook_commands() -> list[str]:
    """Extract all command strings from generate_hooks_json() output."""
    data = generate_hooks_json()
    commands: list[str] = []
    for entry in data.get("hooks", {}).get("PreToolUse", []):
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if cmd:
                commands.append(cmd)
    return commands


@pytest.mark.parametrize("command", _extract_hook_commands(), ids=_extract_hook_commands())
def test_hook_command_executable(command: str) -> None:
    """Every hook command must execute successfully as a subprocess."""
    parts = shlex.split(command)
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


def test_hook_registry_matches_generated_hooks_json() -> None:
    """Every hook in HOOK_REGISTRY must appear in generate_hooks_json() output and vice versa."""
    data = generate_hooks_json()
    generated_pairs: set[tuple[str, str]] = set()
    for entry in data["hooks"]["PreToolUse"]:
        matcher = entry["matcher"]
        for hook in entry["hooks"]:
            cmd = hook["command"]
            script_name = cmd.split("/")[-1]
            generated_pairs.add((matcher, script_name))

    registry_pairs: set[tuple[str, str]] = set()
    for hook_def in HOOK_REGISTRY:
        for script in hook_def.scripts:
            registry_pairs.add((hook_def.matcher, script))

    assert registry_pairs == generated_pairs


def test_hook_registry_scripts_exist_on_disk() -> None:
    """Every script referenced in HOOK_REGISTRY must exist as a file in hooks/."""
    hooks_dir = pkg_root() / "hooks"
    for hook_def in HOOK_REGISTRY:
        for script in hook_def.scripts:
            script_path = hooks_dir / script
            assert script_path.is_file(), f"Registry script not found on disk: {script_path}"
