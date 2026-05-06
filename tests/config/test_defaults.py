"""Settings coherence guard tests (1g).

Validates that natural_exit_grace_seconds and exit_after_stop_delay_ms are
configured coherently so the drain window can always absorb the self-exit delay.
"""

from __future__ import annotations

import pytest
import structlog.testing

from autoskillit.config import AutomationConfig, load_config
from autoskillit.config.settings import RunSkillConfig

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestGraceWindowCoherence:
    """natural_exit_grace_seconds must cover exit_after_stop_delay_ms + margin."""

    def test_natural_exit_grace_covers_exit_after_stop_delay(self) -> None:
        """Default settings: grace_s * 1000 >= exit_after_stop_delay_ms + 500."""
        cfg = AutomationConfig()
        rs = cfg.run_skill
        assert rs.natural_exit_grace_seconds * 1000 >= rs.exit_after_stop_delay_ms + 500, (
            f"natural_exit_grace_seconds={rs.natural_exit_grace_seconds} is too small: "
            f"{rs.natural_exit_grace_seconds * 1000} ms < "
            f"{rs.exit_after_stop_delay_ms + 500} ms (exit_after_stop_delay_ms + 500). "
            "Increase natural_exit_grace_seconds in defaults.yaml."
        )

    def test_settings_validator_rejects_incoherent_grace_window(self) -> None:
        """RunSkillConfig raises ValueError when grace window is too small."""
        with pytest.raises(ValueError, match="natural_exit_grace_seconds"):
            RunSkillConfig(
                natural_exit_grace_seconds=1.0,
                exit_after_stop_delay_ms=2000,
            )

    def test_settings_validator_accepts_coherent_config(self) -> None:
        """RunSkillConfig with sufficient grace window constructs without error."""
        cfg = RunSkillConfig(
            natural_exit_grace_seconds=3.0,
            exit_after_stop_delay_ms=2000,
        )
        # 3.0 * 1000 = 3000 >= 2000 + 500 = 2500 ✓
        assert cfg.natural_exit_grace_seconds == 3.0


class TestIdleOutputTimeoutCoherence:
    """idle_output_timeout default must pass _timeout_coherence_gate."""

    def test_default_idle_output_timeout_passes_coherence_gate(self, tmp_path) -> None:
        """Pure-default config emits zero *_coherence log warnings."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("")
        with structlog.testing.capture_logs() as cap_logs:
            cfg = load_config(tmp_path)
        coherence_warnings = [e for e in cap_logs if "_coherence" in e.get("event", "")]
        assert coherence_warnings == [], (
            f"Default config emitted coherence warnings: {coherence_warnings}. "
            "Update the relevant default in defaults.yaml and _config_dataclasses.py."
        )
        assert cfg.run_skill.idle_output_timeout == 1000


class TestAllDefaultsPassAllGates:
    """Universal guard: shipped defaults must produce zero validation warnings."""

    def test_pure_defaults_emit_no_coherence_warnings(self, tmp_path) -> None:
        """load_config with empty user config emits no *_coherence warnings."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("")
        with structlog.testing.capture_logs() as cap_logs:
            load_config(tmp_path)
        coherence_events = [
            e
            for e in cap_logs
            if e.get("log_level") == "warning" and "_coherence" in e.get("event", "")
        ]
        assert coherence_events == [], (
            f"Default config produced coherence warnings: {coherence_events}. "
            "A shipped default violates its own validation gate. "
            "Fix the default or the gate threshold."
        )


class TestDefaultsSyncYamlDataclass:
    """Numeric defaults in dataclasses must match defaults.yaml."""

    def test_run_skill_defaults_match_yaml(self, tmp_path) -> None:
        """RunSkillConfig field defaults agree with defaults.yaml values."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("")
        cfg = load_config(tmp_path)
        dc = RunSkillConfig()
        assert cfg.run_skill.timeout == dc.timeout
        assert cfg.run_skill.stale_threshold == dc.stale_threshold
        assert cfg.run_skill.idle_output_timeout == dc.idle_output_timeout
        assert cfg.run_skill.exit_after_stop_delay_ms == dc.exit_after_stop_delay_ms
        assert cfg.run_skill.natural_exit_grace_seconds == dc.natural_exit_grace_seconds
        assert cfg.run_skill.max_suppression_seconds == dc.max_suppression_seconds
        assert cfg.run_skill.completion_drain_timeout == dc.completion_drain_timeout
