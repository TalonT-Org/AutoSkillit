from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_codex_tool_timeout_exceeds_max_session_duration():
    """tool_timeout_sec must exceed the maximum possible session duration."""
    from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig
    from autoskillit.config.settings import compute_codex_mcp_tool_timeout
    from autoskillit.execution.backends._codex_config import CODEX_MCP_TOOL_TIMEOUT_FLOOR

    fc = FleetConfig()
    rs = RunSkillConfig()
    max_fleet_session = fc.default_timeout_sec + fc.max_extension_seconds
    max_skill_session = rs.timeout

    assert CODEX_MCP_TOOL_TIMEOUT_FLOOR >= max_fleet_session
    assert CODEX_MCP_TOOL_TIMEOUT_FLOOR >= max_skill_session

    computed = compute_codex_mcp_tool_timeout(run_skill=rs, fleet=fc)
    assert computed >= max_fleet_session
    assert computed >= max_skill_session

    custom_rs = RunSkillConfig(timeout=14400)
    custom_computed = compute_codex_mcp_tool_timeout(run_skill=custom_rs, fleet=fc)
    assert custom_computed >= 14400

    assert CODEX_MCP_TOOL_TIMEOUT_FLOOR == compute_codex_mcp_tool_timeout()
