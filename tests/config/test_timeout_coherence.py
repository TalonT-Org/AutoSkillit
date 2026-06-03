"""Tests for idle_output_timeout and Codex MCP config coherence validation."""

import pytest
import structlog.testing

from autoskillit.config import load_config
from autoskillit.config.settings import _codex_mcp_timeout_coherence_gate

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestTimeoutCoherenceGate:
    """Tests for _timeout_coherence_gate warning behavior."""

    def test_idle_output_timeout_less_than_tool_max_emits_warning(self, tmp_path):
        """idle_output_timeout < known tool max triggers coherence warning."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        # idle_output_timeout=600, but wait_for_merge_queue recipe override is 900s
        (config_dir / "config.yaml").write_text("run_skill:\n  idle_output_timeout: 600\n")
        with structlog.testing.capture_logs() as cap_logs:
            cfg = load_config(tmp_path)
        assert cfg.run_skill.idle_output_timeout == 600
        assert any("idle_output_timeout_coherence" in entry.get("event", "") for entry in cap_logs)

    def test_idle_output_timeout_zero_skips_coherence_check(self, tmp_path):
        """Disabled watchdog (0) passes coherence check unconditionally."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("run_skill:\n  idle_output_timeout: 0\n")
        with structlog.testing.capture_logs() as cap_logs:
            cfg = load_config(tmp_path)
        assert cfg.run_skill.idle_output_timeout == 0
        assert not any(
            "idle_output_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )

    def test_coherence_gate_warns_on_matched_defaults(self, tmp_path):
        """idle_output_timeout == tool_timeout is incoherent (race condition)."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        # idle_output_timeout=600, wait_for_merge_queue default=600 — exact match
        (config_dir / "config.yaml").write_text("run_skill:\n  idle_output_timeout: 600\n")
        with structlog.testing.capture_logs() as cap_logs:
            cfg = load_config(tmp_path)
        assert cfg.run_skill.idle_output_timeout == 600
        assert any("idle_output_timeout_coherence" in entry.get("event", "") for entry in cap_logs)

    def test_coherence_gate_passes_when_idle_exceeds_tool_max(self, tmp_path):
        """idle_output_timeout > all known tool timeouts passes cleanly."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        # idle_output_timeout=1000 > _MERGE_QUEUE_RECIPE_MAX (900)
        (config_dir / "config.yaml").write_text("run_skill:\n  idle_output_timeout: 1000\n")
        with structlog.testing.capture_logs() as cap_logs:
            cfg = load_config(tmp_path)
        assert cfg.run_skill.idle_output_timeout == 1000
        assert not any(
            "idle_output_timeout_coherence" in entry.get("event", "") for entry in cap_logs
        )


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
