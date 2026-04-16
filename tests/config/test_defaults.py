"""Settings coherence guard tests (1g).

Validates that natural_exit_grace_seconds and exit_after_stop_delay_ms are
configured coherently so the drain window can always absorb the self-exit delay.
"""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.config.settings import RunSkillConfig

pytestmark = [pytest.mark.layer("config")]


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
