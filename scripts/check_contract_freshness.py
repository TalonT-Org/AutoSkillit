"""Pre-commit hook: verify contract cards are fresh for all bundled recipes."""

from __future__ import annotations

from pathlib import Path

import yaml
from yaml import SafeLoader as YamlLoader

from autoskillit.recipe.staleness_cache import compute_recipe_hash

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = PROJECT_ROOT / "src" / "autoskillit" / "recipes"


def load_yaml(path: Path) -> dict | None:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=YamlLoader)


def main() -> int:
    stale = []
    missing = []
    # Collect all recipes that should have contract cards (top-level + campaigns/)
    yaml_paths = sorted(RECIPES_DIR.glob("*.yaml"))
    campaigns_dir = RECIPES_DIR / "campaigns"
    if campaigns_dir.is_dir():
        yaml_paths.extend(sorted(campaigns_dir.glob("*.yaml")))
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
        stored_hash = card.get("recipe_source_hash", "")
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
