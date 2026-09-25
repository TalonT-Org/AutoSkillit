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


# Convention-setter for the recipe-pair empty-universe guard: prints `ERROR: ...`
# to stderr from `main()` because this script has no per-file unit tests — only
# tests/infra/test_script_gate_empty_universe.py drives main() end-to-end.
# Scripts with per-file unit tests expose a list-returning check() instead;
# see scripts/check_pyi_stub_format.py for the contrasted pattern. The same
# convention is mirrored in scripts/check_contract_freshness.py.


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
        if (
            existing == new_content
            and json_path.stat().st_mtime_ns >= yaml_path.stat().st_mtime_ns
        ):
            return False
    json_path.write_text(new_content, encoding="utf-8")
    return True


def _is_current(yaml_path: Path) -> bool:
    """Return True if the JSON counterpart is already up-to-date (content matches)."""
    json_path = yaml_path.with_suffix(".json")
    if not json_path.exists():
        return False
    try:
        data = yaml.load(yaml_path.read_bytes(), Loader=Loader)
    except yaml.YAMLError:
        print(f"WARNING: {yaml_path} has invalid YAML", file=sys.stderr)
        return False
    expected = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if json_path.read_text(encoding="utf-8") != expected:
        return False
    return json_path.stat().st_mtime_ns >= yaml_path.stat().st_mtime_ns


def main() -> int:
    check_only = "--check" in sys.argv
    if not RECIPES_DIR.is_dir():
        print(f"ERROR: recipes dir not found: {RECIPES_DIR}", file=sys.stderr)
        return 1
    yamls = sorted(RECIPES_DIR.rglob("*.yaml"))
    if not yamls:
        print(f"ERROR: no recipe YAML under {RECIPES_DIR}", file=sys.stderr)
        return 1
    if check_only:
        stale = [y for y in yamls if not _is_current(y)]
        if stale:
            for y in sorted(stale):
                print(f"STALE: {y}", file=sys.stderr)
            return 1
        return 0
    count = 0
    errors = 0
    for yaml_path in yamls:
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
