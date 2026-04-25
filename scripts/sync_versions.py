#!/usr/bin/env python3
"""Sync all version-bearing artifacts from pyproject.toml.

Discovers plugin.json and all recipe YAMLs containing autoskillit_version,
then updates each to match the canonical version in pyproject.toml.

Exit 0 if all files are in sync (or were updated).
Exit 1 on error or (in --check mode) if any file is out of sync.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
PLUGIN_JSON = PROJECT_ROOT / "src" / "autoskillit" / ".claude-plugin" / "plugin.json"
RECIPES_DIR = PROJECT_ROOT / "src" / "autoskillit" / "recipes"

_VERSION_LINE_RE = re.compile(r"^(autoskillit_version:\s*)(.+)$", re.MULTILINE)


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


def _sync_recipe(path: Path, version: str, *, check: bool) -> tuple[bool, bool]:
    """Sync a single recipe YAML. Returns (was_stale, updated_ok)."""
    text = path.read_text(encoding="utf-8")
    m = _VERSION_LINE_RE.search(text)
    if m is None:
        return False, True
    current = m.group(2).strip().strip("\"'")
    if current == version:
        return False, True
    if check:
        return True, True
    quoted = f'"{version}"'
    new_text = text[: m.start(2)] + quoted + text[m.end(2) :]
    path.write_text(new_text, encoding="utf-8")
    return True, True


def main() -> int:
    check = "--check" in sys.argv

    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]

    stale: list[str] = []
    errors: list[str] = []

    was_stale, ok = _sync_plugin_json(version, check=check)
    if was_stale:
        stale.append(str(PLUGIN_JSON.relative_to(PROJECT_ROOT)))
    if not ok:
        errors.append("plugin.json")

    for recipe_path in sorted(RECIPES_DIR.rglob("*.yaml")):
        was_stale, ok = _sync_recipe(recipe_path, version, check=check)
        if was_stale:
            stale.append(str(recipe_path.relative_to(PROJECT_ROOT)))
        if not ok:
            errors.append(str(recipe_path.relative_to(PROJECT_ROOT)))

    if errors:
        print(f"Errors updating: {', '.join(errors)}", file=sys.stderr)
        return 1

    if check:
        if stale:
            print(f"Out of sync (expected {version}):")
            for f in stale:
                print(f"  {f}")
            return 1
        print(f"All version artifacts in sync at {version}")
        return 0

    if stale:
        print(f"Updated to {version}:")
        for f in stale:
            print(f"  {f}")
    else:
        print(f"All version artifacts already at {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
