"""Tests for YAML parse-failure write guards in _order.py config helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _write_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestConfigYamlParseFailureGuard:
    def test_enable_packs_refuses_overwrite_on_corrupt_yaml(self, tmp_path: Path) -> None:
        from autoskillit.cli.session._order import _enable_packs_permanently

        config_path = tmp_path / ".autoskillit" / "config.yaml"
        corrupt_content = "{{{invalid"
        _write_config(config_path, corrupt_content)

        with pytest.raises(SystemExit):
            _enable_packs_permanently(tmp_path, frozenset({"pack1"}))

        assert config_path.read_text() == corrupt_content

    def test_enable_subsets_refuses_overwrite_on_corrupt_yaml(self, tmp_path: Path) -> None:
        from autoskillit.cli.session._order import _enable_subsets_permanently

        config_path = tmp_path / ".autoskillit" / "config.yaml"
        corrupt_content = "{{{invalid"
        _write_config(config_path, corrupt_content)

        with pytest.raises(SystemExit):
            _enable_subsets_permanently(tmp_path, frozenset({"subset1"}))

        assert config_path.read_text() == corrupt_content

    def test_enable_packs_works_on_missing_file(self, tmp_path: Path) -> None:
        from autoskillit.cli.session._order import _enable_packs_permanently

        config_path = tmp_path / ".autoskillit" / "config.yaml"
        assert not config_path.exists()

        _enable_packs_permanently(tmp_path, frozenset({"pack1"}))

        assert config_path.exists()
        content = config_path.read_text()
        assert "pack1" in content

    def test_enable_packs_works_on_valid_file(self, tmp_path: Path) -> None:
        from autoskillit.cli.session._order import _enable_packs_permanently

        config_path = tmp_path / ".autoskillit" / "config.yaml"
        _write_config(config_path, "packs:\n  enabled:\n    - existing_pack\n")

        _enable_packs_permanently(tmp_path, frozenset({"pack1"}))

        content = config_path.read_text()
        assert "pack1" in content
        assert "existing_pack" in content
