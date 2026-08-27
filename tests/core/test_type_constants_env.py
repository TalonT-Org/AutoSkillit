"""Tests for env-var constants."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


# ---------------------------------------------------------------------------
# T4: SESSION_TYPE_COOK/ORDER moved to cli/session/_session_constants.py
# ---------------------------------------------------------------------------


def test_session_type_cook_order_not_in_core_types() -> None:
    """SESSION_TYPE_COOK/ORDER are CLI labels, not core type constants."""
    from autoskillit.core.types import _type_constants

    assert not hasattr(_type_constants, "SESSION_TYPE_COOK")
    assert not hasattr(_type_constants, "SESSION_TYPE_ORDER")


def test_claude_code_mcp_tool_idle_timeout_env_var_value() -> None:
    from autoskillit.core.types._type_constants_env import (
        CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR,
    )

    assert CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR == "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT"


def test_claude_code_mcp_tool_idle_timeout_env_var_in_all() -> None:
    from autoskillit.core.types._type_constants_env import __all__ as env_all

    assert "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR" in env_all


def test_claude_code_mcp_tool_idle_timeout_env_var_importable_from_types() -> None:
    # Import-path check is the new coverage; the value is asserted by the
    # _value() test above (re-exports preserve the binding).
    from autoskillit.core.types import CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR  # noqa: F401


def test_claude_code_mcp_tool_idle_timeout_env_var_importable_from_core() -> None:
    # Re-export via autoskillit.core gateway; value already asserted above.
    from autoskillit.core import CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR  # noqa: F401


# ---------------------------------------------------------------------------
# HEADLESS_AUTO_GATE_ENV_VAR constant
# ---------------------------------------------------------------------------


def test_headless_auto_gate_env_var_value() -> None:
    from autoskillit.core.types._type_constants_env import HEADLESS_AUTO_GATE_ENV_VAR

    assert HEADLESS_AUTO_GATE_ENV_VAR == "AUTOSKILLIT_HEADLESS_AUTO_GATE"


def test_headless_auto_gate_env_var_in_all() -> None:
    from autoskillit.core.types._type_constants_env import __all__ as env_all

    assert "HEADLESS_AUTO_GATE_ENV_VAR" in env_all


def test_headless_auto_gate_env_var_importable_from_types() -> None:
    from autoskillit.core.types import HEADLESS_AUTO_GATE_ENV_VAR

    assert HEADLESS_AUTO_GATE_ENV_VAR == "AUTOSKILLIT_HEADLESS_AUTO_GATE"


def test_headless_auto_gate_env_var_importable_from_core() -> None:
    from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR

    assert HEADLESS_AUTO_GATE_ENV_VAR == "AUTOSKILLIT_HEADLESS_AUTO_GATE"


def test_headless_auto_gate_env_var_in_private_env_vars() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_HEADLESS_AUTO_GATE" in AUTOSKILLIT_PRIVATE_ENV_VARS


# ---------------------------------------------------------------------------
# ORDER_INTERACTIVE_REQUIRED_ENV constant (issue #4253 Part A)
# ---------------------------------------------------------------------------


def test_order_interactive_required_env_value() -> None:
    from autoskillit.core.types._type_constants_env import ORDER_INTERACTIVE_REQUIRED_ENV

    assert ORDER_INTERACTIVE_REQUIRED_ENV == frozenset(
        {
            "AUTOSKILLIT_SESSION_TYPE",
            "MAX_MCP_OUTPUT_TOKENS",
            "MCP_CONNECTION_NONBLOCKING",
            "AUTOSKILLIT_AGENT_BACKEND",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND",
        }
    )


def test_order_interactive_required_env_excludes_headless() -> None:
    """Interactive order sessions must not require AUTOSKILLIT_HEADLESS."""
    from autoskillit.core.types._type_constants_env import ORDER_INTERACTIVE_REQUIRED_ENV

    assert "AUTOSKILLIT_HEADLESS" not in ORDER_INTERACTIVE_REQUIRED_ENV


def test_order_interactive_required_env_in_all() -> None:
    from autoskillit.core.types._type_constants_env import __all__ as env_all

    assert "ORDER_INTERACTIVE_REQUIRED_ENV" in env_all


def test_order_interactive_required_env_importable_from_types() -> None:
    from autoskillit.core.types import ORDER_INTERACTIVE_REQUIRED_ENV

    assert "MAX_MCP_OUTPUT_TOKENS" in ORDER_INTERACTIVE_REQUIRED_ENV


def test_order_interactive_required_env_importable_from_core() -> None:
    from autoskillit.core import ORDER_INTERACTIVE_REQUIRED_ENV

    assert "MAX_MCP_OUTPUT_TOKENS" in ORDER_INTERACTIVE_REQUIRED_ENV


# ---------------------------------------------------------------------------
# CODEX_INTERACTIVE_REQUIRED_ENV includes MAX_MCP_OUTPUT_TOKENS (issue #4253 Part A)
# ---------------------------------------------------------------------------


def test_codex_interactive_required_env_includes_max_mcp_output_tokens() -> None:
    from autoskillit.core import CODEX_INTERACTIVE_REQUIRED_ENV

    assert "MAX_MCP_OUTPUT_TOKENS" in CODEX_INTERACTIVE_REQUIRED_ENV


def test_codex_cook_storage_and_environment_constants_are_pinned() -> None:
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_ACTIVE_VIEWS_SUBDIR,
        CODEX_ARCHIVED_SESSIONS_SUBDIR,
        CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR,
        CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR,
        CODEX_COOK_RESERVED_ENV_VARS,
        CODEX_SESSIONS_SUBDIR,
        CODEX_STARTUP_TRACE_ENV_VAR,
    )

    assert (
        CODEX_SESSIONS_SUBDIR,
        CODEX_ARCHIVED_SESSIONS_SUBDIR,
        CODEX_ACTIVE_VIEWS_SUBDIR,
        CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR,
        CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR,
    ) == (
        "codex-sessions",
        "codex-archived-sessions",
        "codex-active-sessions",
        "codex-attempt-reconciliations",
        "codex-attempt-reconciliation-tombstones",
    )
    assert CODEX_COOK_RESERVED_ENV_VARS == frozenset({"CODEX_HOME", "CODEX_SQLITE_HOME"})
    assert CODEX_STARTUP_TRACE_ENV_VAR == "AUTOSKILLIT_CODEX_STARTUP_TRACE"
    assert {
        CODEX_STARTUP_TRACE_ENV_VAR,
        *CODEX_COOK_RESERVED_ENV_VARS,
    } <= AUTOSKILLIT_PRIVATE_ENV_VARS
