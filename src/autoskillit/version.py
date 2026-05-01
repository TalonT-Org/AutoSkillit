"""Version health utilities (IL-0 — import layer 0).

Zero autoskillit imports — this module is importable before any other
autoskillit subpackage.  YAML fields are extracted via regex to avoid
depending on core/io.py (which would violate the IL-0 contract).
"""

from __future__ import annotations

import functools
import importlib.metadata
import importlib.resources as ir
import json
import re
from pathlib import Path

_VERSION_RE = re.compile(r'^autoskillit_version:\s*["\']?([^"\'#\s]+)')


def _extract_recipe_version(path: Path) -> str | None:
    """Extract autoskillit_version from a recipe YAML via line scan."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        m = _VERSION_RE.match(line)
        if m:
            return m.group(1)
    return None


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
            ver = _extract_recipe_version(recipe_path)
            if ver is not None and ver != package_version:
                stale_recipes.append(str(recipe_path.relative_to(recipes_dir)))

    return {
        "package_version": package_version,
        "plugin_json_version": plugin_version,
        "match": package_version == plugin_version,
        "recipe_versions_match": len(stale_recipes) == 0,
        "stale_recipes": stale_recipes,
    }
