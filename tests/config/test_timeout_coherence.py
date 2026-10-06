"""Tests for idle_output_timeout and Codex/Claude MCP config coherence validation."""

import pytest
import structlog.testing

from autoskillit.config.settings import (
    _claude_mcp_timeout_coherence_gate,
    _codex_mcp_timeout_coherence_gate,
)

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


def test_general_idle_timeout_coherence_gate_is_removed() -> None:
    from autoskillit.config import _coherence

    assert not hasattr(_coherence, "_timeout_coherence_gate")


class TestCodexMcpTimeoutCoherenceGate:
    """Tests for _codex_mcp_timeout_coherence_gate warning behavior."""

    def test_no_warning_with_default_configs(self):
        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig

        with structlog.testing.capture_logs() as cap_logs:
            _codex_mcp_timeout_coherence_gate(RunSkillConfig(), FleetConfig())
        assert not any(
            "codex_mcp_tool_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )

    def test_warns_when_tool_timeout_below_max_session(self):
        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig

        rs = RunSkillConfig()
        fc = FleetConfig()
        max_session = max(fc.default_timeout_sec + fc.max_extension_seconds, rs.timeout)
        with structlog.testing.capture_logs() as cap_logs:
            _codex_mcp_timeout_coherence_gate(rs, fc, tool_timeout=max_session - 1)
        assert any(
            "codex_mcp_tool_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )

    def test_mcp_tool_timeout_rejects_non_positive(self):
        """__post_init__ rejects mcp_tool_timeout_sec <= 0."""
        from autoskillit.config._config_dataclasses import RunSkillConfig

        with pytest.raises(ValueError, match="mcp_tool_timeout_sec"):
            RunSkillConfig(mcp_tool_timeout_sec=0)
        with pytest.raises(ValueError, match="mcp_tool_timeout_sec"):
            RunSkillConfig(mcp_tool_timeout_sec=-1.0)

    def test_mcp_tool_timeout_field_below_max_triggers_warning(self):
        """Coherence gate warns when mcp_tool_timeout_sec field is below max session."""
        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig

        rs = RunSkillConfig(mcp_tool_timeout_sec=100.0)
        fc = FleetConfig()
        with structlog.testing.capture_logs() as cap_logs:
            _codex_mcp_timeout_coherence_gate(rs, fc, tool_timeout=rs.mcp_tool_timeout_sec)
        assert any(
            "codex_mcp_tool_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )

    def test_mcp_tool_timeout_field_at_max_passes_cleanly(self):
        """Coherence gate passes when mcp_tool_timeout_sec field is at or above max session."""
        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig

        rs = RunSkillConfig()
        fc = FleetConfig()
        max_session = max(fc.default_timeout_sec + fc.max_extension_seconds, rs.timeout)
        rs_at_max = RunSkillConfig(mcp_tool_timeout_sec=float(max_session))
        with structlog.testing.capture_logs() as cap_logs:
            _codex_mcp_timeout_coherence_gate(
                rs_at_max, fc, tool_timeout=rs_at_max.mcp_tool_timeout_sec
            )
        assert not any(
            "codex_mcp_tool_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )


class TestClaudeMcpTimeoutCoherenceGate:
    """Tests for _claude_mcp_timeout_coherence_gate warning behavior."""

    def test_no_warning_with_default_configs(self):
        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig

        with structlog.testing.capture_logs() as cap_logs:
            _claude_mcp_timeout_coherence_gate(RunSkillConfig(), FleetConfig())
        assert not any(
            "claude_mcp_tool_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )

    def test_warns_when_tool_timeout_below_max_session(self):
        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig

        fc = FleetConfig()
        max_session = max(
            fc.default_timeout_sec + fc.max_extension_seconds, RunSkillConfig().timeout
        )
        rs = RunSkillConfig(mcp_tool_timeout_sec=float(max_session - 1))
        with structlog.testing.capture_logs() as cap_logs:
            _claude_mcp_timeout_coherence_gate(rs, fc)
        assert any(
            "claude_mcp_tool_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )

    def test_mcp_tool_timeout_field_at_max_passes_cleanly(self):
        from autoskillit.config._config_dataclasses import FleetConfig, RunSkillConfig

        fc = FleetConfig()
        max_session = max(
            fc.default_timeout_sec + fc.max_extension_seconds, RunSkillConfig().timeout
        )
        rs_at_max = RunSkillConfig(mcp_tool_timeout_sec=float(max_session))
        with structlog.testing.capture_logs() as cap_logs:
            _claude_mcp_timeout_coherence_gate(rs_at_max, fc)
        assert not any(
            "claude_mcp_tool_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )
