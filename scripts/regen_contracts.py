"""Regenerate contract cards for all bundled recipes."""

from autoskillit.recipe.contracts import generate_recipe_card
from autoskillit.recipe.io import builtin_recipes_dir


def main() -> int:
    recipes_dir = builtin_recipes_dir()
    count = 0
    for yaml_path in sorted(recipes_dir.glob("*.yaml")):
        generate_recipe_card(yaml_path, recipes_dir)
        count += 1
        print(f"  {yaml_path.stem}")
    campaigns_dir = recipes_dir / "campaigns"
    if campaigns_dir.is_dir():
        for yaml_path in sorted(campaigns_dir.glob("*.yaml")):
            generate_recipe_card(yaml_path, recipes_dir)
            count += 1
            print(f"  {yaml_path.stem} (campaign)")
    print(f"\nRegenerated {count} contract cards.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
