"""Cross-file version consistency tests.

Ensures pyproject.toml, __init__.__version__, and plugin.json agree.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import autoskillit


class TestVersionConsistency:
    def test_pyproject_version_matches_init_version(self):
        pyproject = Path(autoskillit.__file__).parent.parent.parent / "pyproject.toml"
        for line in pyproject.read_text().splitlines():
            if line.strip().startswith("version"):
                pyproject_version = line.split("=")[1].strip().strip('"')
                break
        else:
            raise AssertionError("No version field found in pyproject.toml")
        assert pyproject_version == autoskillit.__version__

    def test_plugin_json_version_matches_init_version(self):
        plugin_json = Path(autoskillit.__file__).parent / ".claude-plugin" / "plugin.json"
        data = json.loads(plugin_json.read_text())
        assert data["version"] == autoskillit.__version__

    def test_version_info_reads_plugin_json_only_once(self, tmp_path):
        """@lru_cache ensures plugin.json is read exactly once across multiple calls."""
        from autoskillit.version import version_info

        version_info.cache_clear()
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "9.9.9"}')
        read_count = 0
        original_read_text = Path.read_text

        def counting_read_text(self, *args, **kwargs):
            nonlocal read_count
            if self.name == "plugin.json":
                read_count += 1
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", counting_read_text):
            result1 = version_info(str(tmp_path))
            result2 = version_info(str(tmp_path))

        assert result1 == result2
        assert read_count == 1, f"plugin.json should be read once (got {read_count})"
        version_info.cache_clear()

    def test_marketplace_json_version_field(self, tmp_path, monkeypatch):
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli import _ensure_marketplace

        _ensure_marketplace()
        manifest = (
            tmp_path / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"
        )
        data = json.loads(manifest.read_text())
        plugins = data.get("plugins", [])
        assert len(plugins) > 0
        assert plugins[0]["version"] == autoskillit.__version__
