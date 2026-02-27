"""Path-based recipe metadata utilities for migration_engine."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core.io import load_yaml
from autoskillit.core.types import RecipeSource
from autoskillit.recipe_schema import AUTOSKILLIT_VERSION_KEY, RecipeInfo


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


def parse_recipe_metadata(path: Path) -> RecipeInfo:
    """Extract recipe metadata from a YAML file.

    Handles both single-document YAML and frontmatter format
    (YAML between --- delimiters, followed by arbitrary content).
    """
    text = path.read_text()
    metadata_text = _extract_frontmatter(text)
    data = load_yaml(metadata_text)
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
        version=data.get(AUTOSKILLIT_VERSION_KEY),
    )
