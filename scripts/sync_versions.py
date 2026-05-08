#!/usr/bin/env python3
"""Sync all version-bearing artifacts from pyproject.toml.

Discovers plugin.json and updates it to match the canonical version
in pyproject.toml.

Exit 0 if plugin.json is in sync (or was updated).
Exit 1 on error or (in --check mode) if plugin.json is out of sync.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
PLUGIN_JSON = PROJECT_ROOT / "src" / "autoskillit" / ".claude-plugin" / "plugin.json"


def _sync_plugin_json(version: str, *, check: bool) -> tuple[bool, bool]:
    """Sync plugin.json. Returns (was_stale, updated_ok)."""
    data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    if data.get("version") == version:
        return False, True
    if check:
        return True, True
    data["version"] = version
    content = json.dumps(data, indent=2) + "\n"
    tmp = PLUGIN_JSON.parent / (PLUGIN_JSON.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(PLUGIN_JSON)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"Error updating plugin.json: {e}", file=sys.stderr)
        return True, False
    return True, True


def main() -> int:
    check = "--check" in sys.argv

    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]

    was_stale, ok = _sync_plugin_json(version, check=check)
    if not ok:
        print("Error updating plugin.json", file=sys.stderr)
        return 1

    if check:
        if was_stale:
            print(f"plugin.json is out of sync (expected {version})")
            return 1
        print(f"plugin.json in sync at {version}")
        return 0

    if was_stale:
        print(f"Updated plugin.json to {version}")
    else:
        print(f"plugin.json already at {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
