"""Recipe discovery from .autoskillit/recipes/."""

from __future__ import annotations

from pathlib import Path

import yaml

from autoskillit import recipe_parser
from autoskillit.recipe_parser import RecipeInfo, RecipeSource
from autoskillit.types import LoadReport, LoadResult


def _extract_frontmatter(text: str) -> str:
    """Extract YAML metadata text from a frontmatter document.

    If the text starts with ``---``, returns only the text between
    the opening and closing ``---`` delimiters.  Otherwise returns
    the full text unchanged (plain YAML, no frontmatter).
    """
    if not text.startswith("---"):
        return text
    # Skip the opening "---\n"
    after_open = text.index("\n", 0) + 1
    # Find the closing "---"
    close = text.index("\n---", after_open)
    return text[after_open:close]


def _parse_recipe_metadata(path: Path) -> RecipeInfo:
    """Extract recipe metadata from a YAML file.

    Handles both single-document YAML and frontmatter format
    (YAML between --- delimiters, followed by arbitrary content).
    """
    text = path.read_text()
    metadata_text = _extract_frontmatter(text)
    data = yaml.safe_load(metadata_text)
    if not isinstance(data, dict):
        raise ValueError(f"YAML metadata must be a mapping: {path}")
    name = data.get("name", "")
    if not name:
        raise ValueError(f"Recipe missing required 'name' field: {path}")
    return RecipeInfo(
        name=name,
        description=data.get("description", ""),
        summary=data.get("summary", ""),
        path=path,
        source=RecipeSource.PROJECT,
        version=data.get("autoskillit_version"),
    )


def list_recipes(project_dir: Path) -> LoadResult[RecipeInfo]:
    """Discover recipes from .autoskillit/recipes/."""
    recipes_dir = project_dir / ".autoskillit" / "recipes"
    if not recipes_dir.is_dir():
        return LoadResult(items=[], errors=[])

    items: list[RecipeInfo] = []
    errors: list[LoadReport] = []
    for f in sorted(recipes_dir.iterdir()):
        if f.suffix in (".yaml", ".yml") and f.is_file():
            try:
                info = _parse_recipe_metadata(f)
                items.append(info)
            except Exception as exc:
                errors.append(LoadReport(path=f, error=str(exc)))
    return LoadResult(items=items, errors=errors)


def load_recipe(project_dir: Path, name: str) -> str | None:
    """Load a recipe by name, returning raw YAML content."""
    result = list_recipes(project_dir)
    match = next((r for r in result.items if r.name == name), None)
    if match is None:
        return None
    return match.path.read_text()


def sync_bundled_recipes(project_dir: Path) -> None:
    """Overwrite .autoskillit/recipes/ with bundled recipe YAMLs of the same name.

    Only runs if .autoskillit/ already exists in the project directory.
    Project-specific recipes with no bundled counterpart are left untouched.
    """
    autoskillit_dir = project_dir / ".autoskillit"
    if not autoskillit_dir.is_dir():
        return
    recipes_dir = autoskillit_dir / "recipes"
    recipes_dir.mkdir(exist_ok=True)
    bundled_dir = recipe_parser.builtin_recipes_dir()
    if not bundled_dir.is_dir():
        return
    for src in bundled_dir.glob("*.yaml"):
        (recipes_dir / src.name).write_text(src.read_text())
