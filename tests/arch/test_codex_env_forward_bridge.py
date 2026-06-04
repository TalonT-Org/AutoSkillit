"""Cross-layer bridge: CODEX_MCP_ENV_FORWARD_VARS ↔ cmd builders ↔ capabilities."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND__BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_MCP_CLIENT_BACKEND", raising=False)


def test_codex_forward_vars_subset_of_codex_cmd_env() -> None:
    """Every var in CODEX_MCP_ENV_FORWARD_VARS must be injected by Codex cmd builders."""
    from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS, DirectInstall, OutputFormat, pkg_root
    from autoskillit.execution.backends.codex import CodexBackend

    backend = CodexBackend()
    skill_spec = backend.build_skill_session_cmd(
        skill_command="/test-skill",
        cwd="/work",
        completion_marker="%%DONE%%",
        model=None,
        plugin_source=None,
        output_format=OutputFormat.JSON,
    )
    food_truck_spec = backend.build_food_truck_cmd(
        orchestrator_prompt="dispatch",
        plugin_source=DirectInstall(plugin_dir=pkg_root()),
        cwd="/work",
        completion_marker="%%DONE%%",
    )
    for var in sorted(CODEX_MCP_ENV_FORWARD_VARS):
        assert var in skill_spec.env, (
            f"{var} in CODEX_MCP_ENV_FORWARD_VARS but missing from build_skill_session_cmd env"
        )
        assert var in food_truck_spec.env, (
            f"{var} in CODEX_MCP_ENV_FORWARD_VARS but missing from build_food_truck_cmd env"
        )


def test_always_injected_mcp_server_vars_forwarded_by_codex() -> None:
    """Vars bypassing PRIVATE_ENV_VARS must be in CODEX_MCP_ENV_FORWARD_VARS."""
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_FORWARD_VARS,
        ORCHESTRATOR_SESSION_REQUIRED_ENV,
        SKILL_SESSION_REQUIRED_ENV,
    )

    all_required = SKILL_SESSION_REQUIRED_ENV | ORCHESTRATOR_SESSION_REQUIRED_ENV
    bypass_vars = all_required - AUTOSKILLIT_PRIVATE_ENV_VARS - {"MCP_CONNECTION_NONBLOCKING"}
    missing = bypass_vars - CODEX_MCP_ENV_FORWARD_VARS
    assert not missing, (
        f"Vars bypassing PRIVATE_ENV_VARS but missing from CODEX_MCP_ENV_FORWARD_VARS: {missing}. "
        f"The Codex MCP server won't receive these through config.toml."
    )


def test_codex_capabilities_forward_vars_match_constant() -> None:
    """CodexBackend.capabilities.mcp_env_forward_vars must equal CODEX_MCP_ENV_FORWARD_VARS."""
    from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS
    from autoskillit.execution.backends.codex import CodexBackend

    assert CodexBackend().capabilities.mcp_env_forward_vars == CODEX_MCP_ENV_FORWARD_VARS
