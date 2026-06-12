"""Tests for execution/commands.py re-exports and execution package gateway contracts."""

from __future__ import annotations

import pytest

from autoskillit.core import CmdSpec
from autoskillit.execution.backends._backend_cmd_builder_base import SHARED_BASELINE_ENV

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_session_baseline_env_contains_mcp_connection_nonblocking() -> None:
    assert "MCP_CONNECTION_NONBLOCKING" in SHARED_BASELINE_ENV
    assert SHARED_BASELINE_ENV["MCP_CONNECTION_NONBLOCKING"] == "0"


def test_cmdspec_importable_from_execution() -> None:
    """CmdSpec must be importable from the execution package gateway."""
    from autoskillit.execution import CmdSpec as execution_CmdSpec

    assert execution_CmdSpec is CmdSpec


def test_cmdspec_in_execution_all() -> None:
    """CmdSpec must appear in execution.__all__."""
    import autoskillit.execution as m

    assert "CmdSpec" in m.__all__


def test_max_mcp_output_tokens_value_is_string() -> None:
    """MAX_MCP_OUTPUT_TOKENS in SHARED_BASELINE_ENV must be a non-empty string."""
    assert isinstance(SHARED_BASELINE_ENV["MAX_MCP_OUTPUT_TOKENS"], str)
    assert SHARED_BASELINE_ENV["MAX_MCP_OUTPUT_TOKENS"]
