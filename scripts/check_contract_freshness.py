"""Pre-commit hook: verify contract cards are fresh for all bundled recipes."""

from __future__ import annotations

import hashlib
from pathlib import Path

try:
    from yaml import SafeLoader as YamlLoader
except ImportError:
    from yaml import SafeLoader as YamlLoader  # type: ignore[no-redef,assignment]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = PROJECT_ROOT / "src" / "autoskillit" / "recipes"


def compute_recipe_hash(recipe_path: Path) -> str:
    """sha256 of recipe file bytes, returned as 'sha256:<hex>'."""
    return "sha256:" + hashlib.sha256(recipe_path.read_bytes()).hexdigest()


def load_yaml(path: Path) -> dict:
    """Load a YAML file and return a dict."""
    import yaml

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
        stored_hash = card.get("recipe_source_hash", "")
        current_hash = compute_recipe_hash(yaml_path)
        if stored_hash != current_hash:
            stale.append(name)
    if missing:
        print(f"Missing contract cards: {missing}")
    if stale:
        print(f"Stale contract cards: {stale}")
    if missing or stale:
        print("Run: task compile-recipes  (triggers regen-contracts as a dep)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
