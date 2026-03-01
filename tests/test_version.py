"""Tests for autoskillit.version (P12)."""

import json
from pathlib import Path

import pytest


class TestVersionInfo:
    @pytest.fixture(autouse=True)
    def _clear_version_cache(self):
        from autoskillit.version import version_info

        version_info.cache_clear()
        yield
        version_info.cache_clear()

    def test_returns_expected_keys(self, tmp_path):
        from autoskillit.version import version_info

        info = version_info(plugin_dir=tmp_path)
        assert set(info.keys()) == {"package_version", "plugin_json_version", "match"}

    def test_missing_plugin_json(self, tmp_path):
        from autoskillit.version import version_info

        info = version_info(plugin_dir=tmp_path)
        assert info["plugin_json_version"] is None
        assert info["match"] is False

    def test_matching_versions(self, tmp_path):
        from autoskillit import __version__
        from autoskillit.version import version_info

        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": __version__})
        )
        info = version_info(plugin_dir=tmp_path)
        assert info["match"] is True
        assert info["package_version"] == __version__

    def test_mismatched_versions(self, tmp_path):
        from autoskillit.version import version_info

        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "0.0.0"}))
        info = version_info(plugin_dir=tmp_path)
        assert info["match"] is False
        assert info["plugin_json_version"] == "0.0.0"

    def test_no_args_finds_real_plugin_json(self):
        """Default None → Path(__file__).parent resolves to package dir with real plugin.json."""
        from autoskillit import __version__
        from autoskillit.version import version_info

        info = version_info()
        assert info["package_version"] == __version__
        assert info["plugin_json_version"] == __version__
        assert info["match"] is True

    def test_str_plugin_dir_accepted(self, tmp_path):
        from autoskillit.version import version_info

        info = version_info(plugin_dir=str(tmp_path))
        assert info["plugin_json_version"] is None


class TestVersionArchitecture:
    def test_version_module_has_no_upward_imports(self):
        """version.py must not import any autoskillit submodule except __init__."""
        import ast

        src = (Path(__file__).parent.parent / "src" / "autoskillit" / "version.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    pytest.fail(f"version.py must not import autoskillit.{parts[1]}")

    def test_doctor_imports_version_not_server(self):
        """cli/_doctor.py must import version_info from autoskillit.version, not server."""
        src = (
            Path(__file__).parent.parent / "src" / "autoskillit" / "cli" / "_doctor.py"
        ).read_text()
        assert "from autoskillit.server import version_info" not in src
        assert "from autoskillit.version import version_info" in src
