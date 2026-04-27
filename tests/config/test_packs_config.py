"""Tests for PacksConfig loading and validation."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestPacksConfig:
    # REQ-PACK-003: PacksConfig.enabled default

    def test_packs_config_default_enabled_is_empty_list(self) -> None:
        from autoskillit.config import PacksConfig

        assert PacksConfig().enabled == []

    def test_automation_config_has_packs_field(self) -> None:
        from autoskillit.config import AutomationConfig, PacksConfig

        cfg = AutomationConfig()
        assert isinstance(cfg.packs, PacksConfig)
        assert cfg.packs.enabled == []

    def test_load_config_packs_enabled(self, tmp_path) -> None:
        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("packs:\n  enabled:\n    - research\n")
        config = load_config(tmp_path)
        assert config.packs.enabled == ["research"]

    def test_load_config_packs_enabled_absent_means_empty(self, tmp_path) -> None:
        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("subsets:\n  disabled: []\n")
        config = load_config(tmp_path)
        assert config.packs.enabled == []

    def test_load_config_unknown_pack_in_packs_enabled_logs_warning(self, tmp_path) -> None:
        import logging

        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("packs:\n  enabled:\n    - nonexistent-pack\n")
        captured: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = captured.append  # type: ignore[assignment]
        logger = logging.getLogger("autoskillit.config.settings")  # noqa: TID251
        logger.addHandler(handler)
        try:
            config = load_config(tmp_path)
        finally:
            logger.removeHandler(handler)
        assert config.packs.enabled == ["nonexistent-pack"]  # preserved as-is
        assert any("nonexistent-pack" in r.getMessage() for r in captured)

    def test_write_config_layer_accepts_packs_enabled(self, tmp_path) -> None:
        """write_config_layer does not raise for valid packs.enabled."""
        from autoskillit.config.settings import write_config_layer

        config_path = tmp_path / "config.yaml"
        write_config_layer(config_path, {"packs": {"enabled": ["research"]}})
        assert config_path.exists()
