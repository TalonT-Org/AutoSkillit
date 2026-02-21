"""Cross-file version consistency tests.

Ensures pyproject.toml, __init__.__version__, and plugin.json agree.
"""

from __future__ import annotations

import json
from pathlib import Path

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
        plugin_json = (
            Path(autoskillit.__file__).parent / ".claude-plugin" / "plugin.json"
        )
        data = json.loads(plugin_json.read_text())
        assert data["version"] == autoskillit.__version__

    def test_marketplace_json_version_field(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from autoskillit.cli import _ensure_marketplace

        _ensure_marketplace()
        manifest = tmp_path / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"
        data = json.loads(manifest.read_text())
        plugins = data.get("plugins", [])
        assert len(plugins) > 0
        assert plugins[0]["version"] == autoskillit.__version__
