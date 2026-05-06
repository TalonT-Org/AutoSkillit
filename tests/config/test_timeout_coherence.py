"""Tests for idle_output_timeout config coherence validation."""

import pytest
import structlog.testing

from autoskillit.config import load_config

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
