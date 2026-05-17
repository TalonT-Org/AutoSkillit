"""Pre-compile bundled recipe YAML files to JSON for faster runtime loading."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

try:
    from yaml import CSafeLoader as Loader
except ImportError:
    Loader = yaml.SafeLoader  # type: ignore[misc,assignment]

RECIPES_DIR = Path(__file__).resolve().parent.parent / "src" / "autoskillit" / "recipes"


class CompileError(Exception):
    """Raised when a single recipe file fails to compile."""


def _compile_one(yaml_path: Path) -> bool:
    """Compile one YAML to JSON. Returns True if file was actually written."""
    try:
        data = yaml.load(yaml_path.read_bytes(), Loader=Loader)
    except yaml.YAMLError as exc:
        raise CompileError(f"ERROR: YAML parse failed in {yaml_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CompileError(
            f"ERROR: {yaml_path} top-level value is {type(data).__name__}, expected mapping"
        )
    json_path = yaml_path.with_suffix(".json")
    new_content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if json_path.exists():
        existing = json_path.read_text(encoding="utf-8")
        if existing == new_content:
            return False
    json_path.write_text(new_content, encoding="utf-8")
    return True


def main() -> int:
    if not RECIPES_DIR.is_dir():
        print(f"ERROR: recipes dir not found: {RECIPES_DIR}", file=sys.stderr)
        return 1
    count = 0
    errors = 0
    for yaml_path in sorted(RECIPES_DIR.rglob("*.yaml")):
        try:
            _compile_one(yaml_path)
            count += 1
        except CompileError as exc:
            print(exc, file=sys.stderr)
            errors += 1
    print(f"Compiled {count} YAML files to JSON" + (f" ({errors} errors)" if errors else ""))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
