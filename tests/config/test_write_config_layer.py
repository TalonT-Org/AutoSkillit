"""Tests for write_config_layer atomic write and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestWriteConfigLayer:
    def test_write_config_layer_rejects_secret_key(self, tmp_path: Path) -> None:
        """write_config_layer raises ConfigSchemaError before touching the file."""

        from autoskillit.config.settings import ConfigSchemaError, write_config_layer

        config_path = tmp_path / "config.yaml"
        with pytest.raises(ConfigSchemaError, match="github.token"):
            write_config_layer(config_path, {"github": {"token": "ghp_test"}})
        assert not config_path.exists(), "config.yaml must not be written on schema error"

    def test_write_config_layer_rejects_unknown_key(self, tmp_path: Path) -> None:
        """write_config_layer raises ConfigSchemaError for unknown section."""
        from autoskillit.config.settings import ConfigSchemaError, write_config_layer

        config_path = tmp_path / "config.yaml"
        with pytest.raises(ConfigSchemaError, match="unrecognized key"):
            write_config_layer(config_path, {"invented_section": {"foo": "bar"}})
        assert not config_path.exists()

    def test_write_config_layer_writes_valid_content(self, tmp_path: Path) -> None:
        """write_config_layer writes valid schema content atomically."""
        import yaml as _yaml

        from autoskillit.config.settings import write_config_layer

        config_path = tmp_path / "config.yaml"
        write_config_layer(config_path, {"github": {"default_repo": "owner/repo"}})
        assert config_path.is_file()
        data = _yaml.safe_load(config_path.read_text())
        assert data["github"]["default_repo"] == "owner/repo"

    def test_write_config_layer_accepts_packs_enabled(self, tmp_path: Path) -> None:
        """write_config_layer does not raise for valid packs.enabled."""
        from autoskillit.config.settings import write_config_layer

        config_path = tmp_path / "config.yaml"
        write_config_layer(config_path, {"packs": {"enabled": ["research"]}})
        assert config_path.exists()
