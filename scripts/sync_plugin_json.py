#!/usr/bin/env python3
"""Sync plugin.json version from pyproject.toml.

Reads the version from pyproject.toml and writes it into
src/autoskillit/.claude-plugin/plugin.json atomically.

Exit 0 if the file was updated (or already in sync). Exit 1 on error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
PLUGIN_JSON = PROJECT_ROOT / "src" / "autoskillit" / ".claude-plugin" / "plugin.json"


def main() -> int:
    version = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    data = json.loads(PLUGIN_JSON.read_text())
    if data.get("version") == version:
        print(f"plugin.json already at version {version}")
        return 0
    data["version"] = version
    content = json.dumps(data, indent=2) + "\n"
    tmp = PLUGIN_JSON.parent / (PLUGIN_JSON.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(PLUGIN_JSON)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    print(f"plugin.json updated to version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
