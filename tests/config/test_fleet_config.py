"""Tests for FleetConfig loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.config import AutomationConfig, load_config

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestFleetConfig:
    def test_automation_config_has_fleet_field(self) -> None:
        """AutomationConfig exposes fleet as a FleetConfig."""
        from autoskillit.config.settings import AutomationConfig, FleetConfig

        cfg = AutomationConfig()
        assert isinstance(cfg.fleet, FleetConfig)
        assert cfg.fleet.default_timeout_sec == 3600

    def test_fleet_l2_timeout_matches_defaults_yaml(self) -> None:
        """FleetConfig Python default matches defaults.yaml value."""
        from autoskillit.config.settings import FleetConfig
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        yaml_val = defaults["fleet"]["default_timeout_sec"]
        assert FleetConfig().default_timeout_sec == yaml_val

    def test_load_config_fleet_override(self, tmp_path) -> None:
        """User config with fleet section overrides the default."""
        import yaml

        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"fleet": {"default_timeout_sec": 7200}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.fleet.default_timeout_sec == 7200

    def test_fleet_config_importable_from_config_package(self) -> None:
        """FleetConfig is importable from autoskillit.config."""
        import dataclasses

        from autoskillit.config import FleetConfig

        assert dataclasses.is_dataclass(FleetConfig)

    def test_fleet_key_accepted_by_schema_validator(self, tmp_path) -> None:
        """User config with fleet: section does not raise ConfigSchemaError."""
        import yaml

        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"fleet": {"default_timeout_sec": 1800}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)  # must not raise ConfigSchemaError
        assert cfg.fleet.default_timeout_sec == 1800

    def test_fleet_config_rejects_zero_timeout(self) -> None:
        """FleetConfig raises ValueError when default_timeout_sec is zero."""
        import pytest

        from autoskillit.config.settings import FleetConfig

        with pytest.raises(ValueError, match="default_timeout_sec must be positive"):
            FleetConfig(default_timeout_sec=0).validate(True)

    def test_fleet_config_rejects_negative_timeout(self) -> None:
        """FleetConfig raises ValueError when default_timeout_sec is negative."""
        import pytest

        from autoskillit.config.settings import FleetConfig

        with pytest.raises(ValueError, match="default_timeout_sec must be positive"):
            FleetConfig(default_timeout_sec=-1).validate(True)

    def test_fleet_config_validate_skips_when_feature_disabled(self) -> None:
        """FC_NEW_2: validate(False) does NOT raise even for invalid timeout."""
        from autoskillit.config.settings import FleetConfig

        FleetConfig(default_timeout_sec=0).validate(False)  # must not raise

    def test_fleet_config_construction_no_longer_raises_for_invalid_timeout(self) -> None:
        """FC_NEW_3: FleetConfig(default_timeout_sec=0) constructs without raising."""
        from autoskillit.config.settings import FleetConfig

        cfg = FleetConfig(default_timeout_sec=0)
        assert cfg.default_timeout_sec == 0

    def test_load_config_fleet_invalid_timeout_skips_when_disabled(self, tmp_path: Path) -> None:
        """FC_NEW_4: load_config does NOT raise with invalid timeout when fleet disabled."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {
            "fleet": {"default_timeout_sec": -1},
            "features": {"fleet": False},
        }
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)  # must not raise
        assert cfg.fleet.default_timeout_sec == -1

    def test_load_config_fleet_invalid_timeout_raises_when_enabled(self, tmp_path: Path) -> None:
        """FC_NEW_5: load_config raises ValueError with invalid timeout when fleet enabled."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {
            "fleet": {"default_timeout_sec": -1},
            "features": {"fleet": True},
        }
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        with pytest.raises(ValueError, match="default_timeout_sec must be positive"):
            load_config(tmp_path)


def test_project_config_experimental_enabled_or_fleet_enabled() -> None:
    """Integration's resolved config enables fleet via experimental_enabled blanket."""
    from pathlib import Path

    from autoskillit.config.settings import load_config
    from autoskillit.core.feature_flags import is_feature_enabled

    project_root = Path(__file__).resolve().parents[2]
    if not (project_root / ".autoskillit" / "config.yaml").exists():
        pytest.skip("project config not present (clean clone or CI)")
    cfg = load_config(project_root)
    assert (
        is_feature_enabled("fleet", cfg.features, experimental_enabled=cfg.experimental_enabled)
        is True
    )


def test_config_resolution_fleet_enabled_via_experimental() -> None:
    """Full config resolution enables fleet via experimental_enabled=True from defaults."""
    from pathlib import Path

    from autoskillit.config.settings import load_config
    from autoskillit.core.feature_flags import is_feature_enabled

    project_root = Path(__file__).resolve().parents[2]
    if not (project_root / ".autoskillit" / "config.yaml").exists():
        pytest.skip("project config not present (clean clone or CI)")
    cfg = load_config(project_root)
    assert cfg.experimental_enabled is True
    assert (
        is_feature_enabled("fleet", cfg.features, experimental_enabled=cfg.experimental_enabled)
        is True
    )
