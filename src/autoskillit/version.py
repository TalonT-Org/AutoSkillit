"""Version health utilities (IL-0 — import layer 0).

Zero autoskillit imports — this module is importable before any other
autoskillit subpackage.
"""

from __future__ import annotations

import functools
import importlib.metadata
import importlib.resources as ir
import json
from pathlib import Path


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

    return {
        "package_version": package_version,
        "plugin_json_version": plugin_version,
        "match": package_version == plugin_version,
    }
