"""
Contract tests for Claude CLI flags.

These tests assert that ClaudeFlags constants have the known-correct string
values and that every flag produced by command builders is a registered constant.
If the claude CLI renames a flag, update the constant value here — one change,
one place, and all tests catch the ripple.
"""

from autoskillit.core import ClaudeFlags
from autoskillit.execution.commands import (
    build_headless_cmd,
    build_interactive_cmd,
)

# ---------------------------------------------------------------------------
# Part 1: Constant value contracts — each assertion is the ground truth.
# Any typo or wrong value in ClaudeFlags.* fails here immediately.
# ---------------------------------------------------------------------------


class TestClaudeFlagValues:
    def test_allow_dangerously_skip_permissions_value(self):
        assert (
            ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS
            == "--allow-dangerously-skip-permissions"
        )

    def test_dangerously_skip_permissions_value(self):
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS == "--dangerously-skip-permissions"

    def test_print_flag_value(self):
        assert ClaudeFlags.PRINT == "-p"

    def test_model_flag_value(self):
        assert ClaudeFlags.MODEL == "--model"

    def test_plugin_dir_flag_value(self):
        assert ClaudeFlags.PLUGIN_DIR == "--plugin-dir"

    def test_output_format_flag_value(self):
        assert ClaudeFlags.OUTPUT_FORMAT == "--output-format"

    def test_verbose_flag_value(self):
        assert ClaudeFlags.VERBOSE == "--verbose"

    def test_add_dir_flag_value(self):
        assert ClaudeFlags.ADD_DIR == "--add-dir"

    def test_tools_flag_value(self):
        assert ClaudeFlags.TOOLS == "--tools"

    def test_append_system_prompt_flag_value(self):
        assert ClaudeFlags.APPEND_SYSTEM_PROMPT == "--append-system-prompt"


# ---------------------------------------------------------------------------
# Part 2: Flag registry audit — assembled commands use only known flags.
# An unknown flag in a command builder immediately fails this test.
# ---------------------------------------------------------------------------

KNOWN_CLAUDE_FLAGS: frozenset[str] = frozenset(ClaudeFlags)


def _extract_flags(cmd: list[str]) -> set[str]:
    """Return the set of CLI flag tokens from a command list."""
    return {tok for tok in cmd if tok.startswith("-")}


class TestFlagRegistryAudit:
    def test_interactive_cmd_flags_are_all_registered(self):
        result = build_interactive_cmd(model="claude-sonnet-4-6")
        unknown = _extract_flags(result.cmd) - KNOWN_CLAUDE_FLAGS
        assert not unknown, (
            f"build_interactive_cmd() produced unregistered flags: {unknown!r}. "
            f"Add them to ClaudeFlags before shipping."
        )

    def test_headless_cmd_flags_are_all_registered(self):
        result = build_headless_cmd("test prompt", model="claude-sonnet-4-6")
        unknown = _extract_flags(result.cmd) - KNOWN_CLAUDE_FLAGS
        assert not unknown, (
            f"build_headless_cmd() produced unregistered flags: {unknown!r}. "
            f"Add them to ClaudeFlags before shipping."
        )

    def test_interactive_cmd_uses_correct_permission_flag(self):
        result = build_interactive_cmd()
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in result.cmd
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in result.cmd

    def test_headless_cmd_uses_correct_permission_flag(self):
        result = build_headless_cmd("test prompt")
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in result.cmd
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in result.cmd
