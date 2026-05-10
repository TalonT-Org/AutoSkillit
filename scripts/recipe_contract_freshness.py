#!/usr/bin/env python3
"""Block commits when bundled recipe YAMLs lack a fresh contract card.

Triggered by pre-commit on changes to src/autoskillit/recipes/*.yaml (excluding
the contracts/ subdirectory). For each recipe YAML, resolves the corresponding
contract card in contracts/ and checks freshness via check_contract_staleness().
Exits non-zero if any contract is stale or missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_SRC_ROOT = _PROJECT_ROOT / "src"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0

    sys.path.insert(0, str(_SRC_ROOT))
    from autoskillit.recipe.contracts import check_contract_staleness, load_recipe_card
    from autoskillit.recipe.io import load_recipe

    recipes_dir = _SRC_ROOT / "autoskillit" / "recipes"
    failed = False
    for recipe_path_str in argv[1:]:
        recipe_path = Path(recipe_path_str).resolve()
        if not recipe_path.is_absolute():
            recipe_path = _PROJECT_ROOT / recipe_path_str
        if not str(recipe_path).startswith(str(recipes_dir)):
            continue
        contract = load_recipe_card(recipe_path.stem, recipes_dir)
        if contract is None:
            print(
                f"recipe_contract_freshness: missing contract for {recipe_path.name} — "
                f"run 'generate_recipe_card()' to create it",
                file=sys.stderr,
            )
            failed = True
            continue

        try:
            recipe = load_recipe(recipe_path)
        except Exception as exc:
            print(
                f"recipe_contract_freshness: could not load recipe {recipe_path.name}: {exc}",
                file=sys.stderr,
            )
            failed = True
            continue

        stale_items = check_contract_staleness(contract, recipe_path=recipe_path, cache_path=None)
        if stale_items:
            reasons = ", ".join(sorted(set(item.reason for item in stale_items)))
            print(
                f"recipe_contract_freshness: {recipe_path.name} contract is stale ({reasons}) — "
                f"regenerate with generate_recipe_card()",
                file=sys.stderr,
            )
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
