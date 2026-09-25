"""Pre-commit hook: verify contract cards are fresh for all bundled recipes."""

from __future__ import annotations

import sys
from pathlib import Path

from autoskillit.core.io import load_yaml
from autoskillit.recipe.staleness_cache import compute_recipe_hash

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = PROJECT_ROOT / "src" / "autoskillit" / "recipes"


# This script prints to stderr (no `check()` returning a list) because it has
# no per-file unit tests — only tests/infra/test_script_gate_empty_universe.py
# drives main() end-to-end. Scripts with per-file unit tests expose a
# list-returning check() instead; see scripts/check_pyi_stub_format.py for
# the contrasted pattern. The same convention applies to compile_recipes.py.


def _recipe_yaml_paths() -> list[Path]:
    # Collect all recipes that should have contract cards (top-level + campaigns/)
    yaml_paths = sorted(RECIPES_DIR.glob("*.yaml"))
    campaigns_dir = RECIPES_DIR / "campaigns"
    if campaigns_dir.is_dir():
        yaml_paths.extend(sorted(campaigns_dir.glob("*.yaml")))
    return yaml_paths


def main() -> int:
    stale = []
    missing = []
    yaml_paths = _recipe_yaml_paths()
    if not yaml_paths:
        # Match the compile_recipes.py convention: "ERROR: <verb> <noun> ...".
        print(f"ERROR: no recipe YAML under {RECIPES_DIR}", file=sys.stderr)
        return 1
    for yaml_path in yaml_paths:
        name = yaml_path.stem
        card_path = RECIPES_DIR / "contracts" / f"{name}.yaml"
        if not card_path.is_file():
            missing.append(name)
            continue
        card = load_yaml(card_path)
        if not isinstance(card, dict):
            missing.append(name)
            continue
        stored_hash = card.get("recipe_source_hash")
        if stored_hash is None:
            continue
        current_hash = compute_recipe_hash(yaml_path)
        if stored_hash != current_hash:
            stale.append(name)
    if missing:
        print(f"Missing contract cards: {missing}")
    if stale:
        print(f"Stale contract cards: {stale}")
    if missing or stale:
        print("Run: task regen-contracts")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
