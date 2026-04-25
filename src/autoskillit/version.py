"""Version health utilities (Layer 0)."""

from __future__ import annotations

import functools
import importlib.metadata
import importlib.resources as ir
import json
from pathlib import Path

import yaml


@functools.lru_cache(maxsize=1)
def version_info(plugin_dir: Path | str | None = None) -> dict:
    """Return version health for the autoskillit installation.

    Args:
        plugin_dir: Root of the plugin directory (must contain .claude-plugin/).
            When None, defaults to the autoskillit package directory.
    """
    package_version = importlib.metadata.version("autoskillit")
    if plugin_dir is None:
        plugin_dir = Path(str(ir.files("autoskillit")))
    plugin_dir = Path(plugin_dir)
    plugin_json_path = plugin_dir / ".claude-plugin" / "plugin.json"
    plugin_version = None
    if plugin_json_path.is_file():
        data = json.loads(plugin_json_path.read_text())
        plugin_version = data.get("version")

    stale_recipes: list[str] = []
    recipes_dir = plugin_dir / "recipes"
    if recipes_dir.is_dir():
        for recipe_path in sorted(recipes_dir.rglob("*.yaml")):
            try:
                recipe_data = yaml.safe_load(recipe_path.read_text())
            except Exception:
                continue
            if not isinstance(recipe_data, dict):
                continue
            ver = recipe_data.get("autoskillit_version")
            if ver is not None and ver != package_version:
                stale_recipes.append(str(recipe_path.relative_to(recipes_dir)))

    return {
        "package_version": package_version,
        "plugin_json_version": plugin_version,
        "match": package_version == plugin_version,
        "recipe_versions_match": len(stale_recipes) == 0,
        "stale_recipes": stale_recipes,
    }
