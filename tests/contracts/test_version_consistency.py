"""Cross-file version consistency tests.

Ensures pyproject.toml, __init__.__version__, and plugin.json agree.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit
from autoskillit.core.io import load_yaml
from tests.cli._upgrade_fixtures import (
    CONTAINED_STATES,
    LEGACY_HOME_STATES,
    seed_legacy_home,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


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

    def test_bundled_recipes_do_not_have_autoskillit_version(self):
        recipes_dir = Path(autoskillit.__file__).parent / "recipes"
        has_field = {}
        for recipe_path in sorted(recipes_dir.rglob("*.yaml")):
            data = load_yaml(recipe_path)
            if not isinstance(data, dict):
                continue
            if "autoskillit_version" in data:
                has_field[str(recipe_path.relative_to(recipes_dir))] = data["autoskillit_version"]
        assert not has_field, (
            f"Bundled recipes must NOT declare autoskillit_version "
            f"(field is only for project-local recipes): {has_field}"
        )

    @pytest.mark.parametrize("legacy_state", sorted(LEGACY_HOME_STATES - CONTAINED_STATES))
    def test_marketplace_json_version_field(self, legacy_state, tmp_path, monkeypatch):
        """Install-over-something, not just install-from-nothing.

        Parameterized over the upgrade matrix because the clean-slate fixture is
        exactly what hid F1: `~/.autoskillit/marketplace/plugins/autoskillit` had
        existed on the reporting machine since 2026-02-19 and was a symlink since
        2026-07-20, a state no test could construct.
        """
        seed_legacy_home(legacy_state, tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from autoskillit.cli.install._marketplace import _ensure_marketplace

        _ensure_marketplace()
        manifest = (
            tmp_path / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"
        )
        data = json.loads(manifest.read_text())
        plugins = data.get("plugins", [])
        assert len(plugins) == 1
        assert plugins[0]["version"] == autoskillit.__version__

    @pytest.mark.parametrize("legacy_state", sorted(LEGACY_HOME_STATES - CONTAINED_STATES))
    def test_marketplace_public_projection_matches_private_contract(
        self, legacy_state, tmp_path, monkeypatch
    ):
        """Build guard: published skills are sanitized and manifest-complete.

        Runs over every pre-existing on-disk state an upgrade can land on, not
        just an empty home directory.
        """
        import importlib as _importlib

        from autoskillit.core import SkillSource, pkg_root
        from autoskillit.workspace import (
            DefaultSkillResolver,
            validate_sanitized_plugin_artifact,
        )

        seed_legacy_home(legacy_state, tmp_path)
        marketplace = _importlib.import_module("autoskillit.cli.install._marketplace")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        marketplace_root = marketplace._ensure_marketplace()
        public_root = marketplace_root / "plugins" / "autoskillit"
        private_manifest = (
            marketplace_root / "plugins" / ".autoskillit.autoskillit-projection.json"
        )
        catalog = tuple(
            skill
            for skill in DefaultSkillResolver().list_all()
            if skill.source is SkillSource.BUNDLED
        )
        errors = validate_sanitized_plugin_artifact(
            pkg_root(),
            public_root,
            private_manifest,
            catalog,
        )
        assert not errors, "\n".join(errors)
