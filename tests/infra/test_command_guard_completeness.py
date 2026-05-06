"""Structural enforcement: command-inspecting guards must cover all command-executing tools.

This meta-test ensures that any guard which inspects shell command text:
1. Is invoked for ALL tools that can execute shell commands (Bash, run_cmd)
2. Reads command text from BOTH key formats (command, cmd)
3. Covers all relevant deny patterns

Without this structural test, a guard can be written that reads ``tool_input.cmd``
but not ``tool_input.command``, and it will silently bypass the Bash tool.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

# The canonical set of tool patterns that can execute shell commands.
# When Claude Code calls the native Bash tool: tool_name="Bash", command in tool_input
# When Claude Code calls run_cmd MCP tool: tool_name="mcp__...__run_cmd", cmd in tool_input
COMMAND_EXECUTING_TOOLS = {"Bash", "mcp__autoskillit__local__autoskillit__run_cmd"}


def _find_command_inspecting_guards() -> list[tuple[str, Path]]:
    """Discover guards that inspect shell command text.

    Returns list of (guard_name, guard_script_path) tuples.
    """
    guards_dir = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "hooks" / "guards"
    found = []
    for script_path in sorted(guards_dir.glob("*.py")):
        if script_path.name.startswith("_"):
            continue
        source = script_path.read_text()
        if (
            '.get("cmd"' in source
            or ".get('cmd'" in source
            or '.get("command"' in source
            or ".get('command'" in source
        ):
            guard_name = script_path.stem
            found.append((guard_name, script_path))
    return found


COMMAND_INSPECTING_GUARDS = _find_command_inspecting_guards()


def _get_hookdef_matcher(guard_name: str) -> str | None:
    """Get the matcher regex for a guard from HOOK_REGISTRY."""
    from autoskillit.hook_registry import HOOK_REGISTRY

    for hook_def in HOOK_REGISTRY:
        if any(script.endswith(f"/{guard_name}.py") for script in hook_def.scripts):
            return hook_def.matcher
    return None


class TestCommandGuardStructuralEnforcement:
    """Structural tests ensuring command-inspecting guards cover all command-executing tools."""

    def test_command_inspecting_guards_cover_all_command_tools(self) -> None:
        """Every command-inspecting guard must have a matcher that covers Bash AND run_cmd.

        If a guard reads command text, it must fire for ALL tools that send command text.
        A matcher that only covers run_cmd will silently miss Bash tool invocations.
        """
        for guard_name, _ in COMMAND_INSPECTING_GUARDS:
            matcher = _get_hookdef_matcher(guard_name)
            assert matcher is not None, f"Guard {guard_name} not found in HOOK_REGISTRY"

            pattern = re.compile(matcher)

            # Check that the matcher covers Bash
            bash_matches = pattern.fullmatch("Bash")
            # Check that the matcher covers a representative run_cmd FQN
            run_cmd_matches = pattern.fullmatch("mcp__autoskillit__local__autoskillit__run_cmd")

            assert bash_matches is not None, (
                f"Guard {guard_name} reads command text but its matcher "
                f"{matcher!r} does not match 'Bash'. "
                f"Command-inspecting guards must fire for ALL command-executing tools."
            )
            assert run_cmd_matches is not None, (
                f"Guard {guard_name} reads command text but its matcher "
                f"{matcher!r} does not match 'mcp__autoskillit__local__autoskillit__run_cmd'. "
                f"Command-inspecting guards must fire for ALL command-executing tools."
            )

    def test_command_inspecting_guards_read_both_key_formats(self) -> None:
        """Every command-inspecting guard must read BOTH 'command' and 'cmd' keys.

        Bash tool sends: tool_input.command
        run_cmd MCP tool sends: tool_input.cmd

        A guard that only reads one key will silently miss the other tool.
        The correct pattern is: tool_input.get("command", "") or tool_input.get("cmd", "")
        """
        for guard_name, script_path in COMMAND_INSPECTING_GUARDS:
            source = script_path.read_text()

            has_command_get = (
                '.get("command"' in source
                or ".get('command'" in source
                or 'tool_input.get("command"' in source
                or "tool_input.get('command'" in source
            )
            has_cmd_get = (
                '.get("cmd"' in source
                or ".get('cmd'" in source
                or 'tool_input.get("cmd"' in source
                or "tool_input.get('cmd'" in source
            )

            assert has_command_get and has_cmd_get, (
                f"Guard {guard_name} must read both 'command' and 'cmd' keys from tool_input. "
                f"Found command get: {has_command_get}, Found cmd get: {has_cmd_get}. "
                f"Use: tool_input.get('command', '') or tool_input.get('cmd', '')"
            )
