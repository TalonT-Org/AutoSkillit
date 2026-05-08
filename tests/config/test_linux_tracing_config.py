"""Tests for LinuxTracingConfig loading and validation."""

import dataclasses

import pytest

from autoskillit.config import AutomationConfig, load_config
from autoskillit.config.settings import LinuxTracingConfig

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestLinuxTracingConfig:
    """LinuxTracingConfig dataclass and YAML loading."""

    def test_linux_tracing_config_defaults(self, tmp_path):
        """LT_C1: LinuxTracingConfig defaults: enabled, 5s interval, empty log_dir."""
        cfg = load_config(tmp_path)
        assert cfg.linux_tracing.enabled is True
        assert cfg.linux_tracing.proc_interval == 5.0
        assert cfg.linux_tracing.log_dir == ""

    def test_linux_tracing_config_from_yaml(self, tmp_path):
        """LT_C2: LinuxTracingConfig reads from project config."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "linux_tracing:\n  enabled: true\n  proc_interval: 2.0\n  log_dir: /custom/logs\n"
        )
        cfg = load_config(tmp_path)
        assert cfg.linux_tracing.enabled is True
        assert cfg.linux_tracing.proc_interval == 2.0
        assert cfg.linux_tracing.log_dir == "/custom/logs"

    def test_automation_config_has_linux_tracing_field(self):
        """LT_C3: AutomationConfig has linux_tracing sub-config."""
        cfg = AutomationConfig()
        assert cfg.linux_tracing.enabled is True
        assert cfg.linux_tracing.proc_interval == 5.0
        assert cfg.linux_tracing.log_dir == ""

    def test_linux_tracing_config_fields(self):
        """LT_C4: LinuxTracingConfig has exactly the expected fields."""
        names = {f.name for f in dataclasses.fields(LinuxTracingConfig)}
        assert names == {"enabled", "proc_interval", "log_dir", "tmpfs_path", "max_sessions"}

    def test_linux_tracing_max_sessions_default(self):
        """LT_C5: LinuxTracingConfig includes max_sessions with default 2000."""
        cfg = LinuxTracingConfig(tmpfs_path="/tmp/test")
        assert cfg.max_sessions == 2000

    def test_linux_tracing_max_sessions_yaml_roundtrip(self, tmp_path):
        """LT_C6: max_sessions survives YAML round-trip."""
        (tmp_path / ".autoskillit").mkdir()
        (tmp_path / ".autoskillit" / "config.yaml").write_text(
            "linux_tracing:\n  max_sessions: 750\n"
        )
        cfg = load_config(tmp_path)
        assert cfg.linux_tracing.max_sessions == 750

    def test_automation_config_linux_tracing_has_max_sessions(self):
        """LT_C3 extension: AutomationConfig().linux_tracing includes max_sessions."""
        cfg = AutomationConfig()
        assert cfg.linux_tracing.max_sessions == 2000
