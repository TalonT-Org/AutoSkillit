"""Cross-builder invariants: _HEADLESS_EXCLUSIVE_VARS membership, completion marker position."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import ClaudeFlags, DirectInstall, OutputFormat
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.execution.commands import (
    _HEADLESS_EXCLUSIVE_VARS,
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_headless_exclusive_vars_contains_max_mcp_output_tokens() -> None:
    """MAX_MCP_OUTPUT_TOKENS must be in _HEADLESS_EXCLUSIVE_VARS."""
    assert "MAX_MCP_OUTPUT_TOKENS" in _HEADLESS_EXCLUSIVE_VARS


@pytest.mark.parametrize(
    "builder_call",
    [
        lambda: ClaudeCodeBackend().build_interactive_cmd(),
        lambda: ClaudeCodeBackend().build_skill_session_cmd(
            "/investigate foo",
            cwd="/tmp",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format=OutputFormat.STREAM_JSON,
        ),
        lambda: ClaudeCodeBackend().build_resume_cmd(resume_session_id="abc", prompt="Emit"),
        lambda: ClaudeCodeBackend().build_food_truck_cmd(
            orchestrator_prompt="You are an L3 orchestrator",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
        ),
        lambda: CodexBackend().build_skill_session_cmd(
            "/investigate foo",
            cwd="/tmp",
            completion_marker="%%DONE%%",
        ),
        lambda: CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="L3 orchestrator",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
        ),
    ],
    ids=[
        "interactive",
        "skill_headless",
        "headless_resume",
        "food_truck",
        "codex_skill",
        "codex_food_truck",
    ],
)
def test_all_session_builders_inject_max_mcp_output_tokens(builder_call) -> None:
    """Every session command builder must produce env with MAX_MCP_OUTPUT_TOKENS."""
    spec = builder_call()
    assert "MAX_MCP_OUTPUT_TOKENS" in spec.env
    assert spec.env["MAX_MCP_OUTPUT_TOKENS"] == _MAX_MCP_OUTPUT_TOKENS_VALUE


@pytest.mark.parametrize(
    "builder_call",
    [
        lambda: ClaudeCodeBackend().build_interactive_cmd(),
        lambda: ClaudeCodeBackend().build_skill_session_cmd(
            "/investigate foo",
            cwd="/tmp",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format=OutputFormat.STREAM_JSON,
        ),
        lambda: ClaudeCodeBackend().build_resume_cmd(resume_session_id="abc", prompt="Emit"),
        lambda: ClaudeCodeBackend().build_food_truck_cmd(
            orchestrator_prompt="You are an L3 orchestrator",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
        ),
        lambda: CodexBackend().build_skill_session_cmd(
            "/investigate foo",
            cwd="/tmp",
            completion_marker="%%DONE%%",
        ),
        lambda: CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="L3 orchestrator",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
        ),
    ],
    ids=[
        "interactive",
        "skill_headless",
        "headless_resume",
        "food_truck",
        "codex_skill",
        "codex_food_truck",
    ],
)
def test_all_session_builders_inject_mcp_connection_nonblocking(builder_call) -> None:
    """Every session command builder must produce env with MCP_CONNECTION_NONBLOCKING=0."""
    spec = builder_call()
    assert "MCP_CONNECTION_NONBLOCKING" in spec.env
    assert spec.env["MCP_CONNECTION_NONBLOCKING"] == "0"


def test_launch_id_in_headless_exclusive_vars() -> None:
    assert "AUTOSKILLIT_LAUNCH_ID" in _HEADLESS_EXCLUSIVE_VARS


def test_allowed_write_prefix_in_headless_exclusive_vars() -> None:
    assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" in _HEADLESS_EXCLUSIVE_VARS


def test_allowed_write_prefixes_in_headless_exclusive_vars() -> None:
    assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES" in _HEADLESS_EXCLUSIVE_VARS


def test_skill_name_in_headless_exclusive_vars() -> None:
    assert "AUTOSKILLIT_SKILL_NAME" in _HEADLESS_EXCLUSIVE_VARS


def test_provider_profile_in_headless_exclusive_vars() -> None:
    """AUTOSKILLIT_PROVIDER_PROFILE must be headless-exclusive."""
    assert "AUTOSKILLIT_PROVIDER_PROFILE" in _HEADLESS_EXCLUSIVE_VARS


def test_anthropic_base_url_in_headless_exclusive_vars() -> None:
    """ANTHROPIC_BASE_URL must be headless-exclusive."""
    assert "ANTHROPIC_BASE_URL" in _HEADLESS_EXCLUSIVE_VARS


def test_anthropic_api_key_in_headless_exclusive_vars() -> None:
    """ANTHROPIC_API_KEY must be headless-exclusive."""
    assert "ANTHROPIC_API_KEY" in _HEADLESS_EXCLUSIVE_VARS


def test_anthropic_auth_token_in_headless_exclusive_vars() -> None:
    """ANTHROPIC_AUTH_TOKEN must be headless-exclusive."""
    assert "ANTHROPIC_AUTH_TOKEN" in _HEADLESS_EXCLUSIVE_VARS


def test_stream_idle_timeout_in_headless_exclusive_vars() -> None:
    """CLAUDE_STREAM_IDLE_TIMEOUT_MS must be headless-exclusive."""
    assert "CLAUDE_STREAM_IDLE_TIMEOUT_MS" in _HEADLESS_EXCLUSIVE_VARS


class TestCompletionReminderPositionInvariant:
    """Parametrized invariant: completion marker must appear in final prompt blocks."""

    @pytest.mark.parametrize(
        "build_spec",
        [
            lambda marker: ClaudeCodeBackend().build_skill_session_cmd(
                "/autoskillit:make-plan arg",
                cwd="/repo",
                completion_marker=marker,
                model=None,
                plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
                output_format=OutputFormat.JSON,
                profile_name="minimax",
            ),
            lambda marker: ClaudeCodeBackend().build_skill_session_cmd(
                "/autoskillit:make-plan arg",
                cwd="/repo",
                completion_marker=marker,
                model=None,
                plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
                output_format=OutputFormat.JSON,
                profile_name="minimax",
                resume_session_id="sess-abc",
            ),
            lambda marker: ClaudeCodeBackend().build_food_truck_cmd(
                orchestrator_prompt="Run the campaign",
                plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
                cwd="/repo",
                completion_marker=marker,
            ),
        ],
        ids=["skill_session_non_resume", "skill_session_resume", "food_truck_non_resume"],
    )
    def test_marker_in_final_two_blocks(self, build_spec) -> None:
        """Completion marker must appear in the last two \\n\\n-delimited blocks."""
        marker = "%%ORDER_UP::test123%%"
        spec = build_spec(marker)
        prompt_idx = spec.cmd.index(ClaudeFlags.PRINT) + 1
        prompt = spec.cmd[prompt_idx]
        blocks = prompt.split("\n\n")
        last_two = "\n\n".join(blocks[-2:])
        assert marker in last_two, (
            f"Completion marker must appear in the last two prompt blocks. "
            f"Last block: {blocks[-1][:100]!r}"
        )


def test_session_deadline_in_headless_exclusive_vars() -> None:
    assert "AUTOSKILLIT_SESSION_DEADLINE" in _HEADLESS_EXCLUSIVE_VARS


def test_headless_exclusive_vars_contains_claude_code_subagent_model() -> None:
    """CLAUDE_CODE_SUBAGENT_MODEL must be in _HEADLESS_EXCLUSIVE_VARS to block host-env leakage."""
    assert "CLAUDE_CODE_SUBAGENT_MODEL" in _HEADLESS_EXCLUSIVE_VARS


def test_cwd_in_headless_exclusive_vars() -> None:
    """AUTOSKILLIT_CWD must be in _HEADLESS_EXCLUSIVE_VARS."""
    assert "AUTOSKILLIT_CWD" in _HEADLESS_EXCLUSIVE_VARS


def test_session_deadline_not_in_l1_subprocess_env(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_SESSION_DEADLINE", "9999999999.0")
    spec = ClaudeCodeBackend().build_skill_session_cmd(
        "/investigate foo",
        cwd="/tmp",
        completion_marker="%%DONE%%",
        model=None,
        plugin_source=None,
        output_format=OutputFormat.STREAM_JSON,
    )
    assert "AUTOSKILLIT_SESSION_DEADLINE" not in spec.env
