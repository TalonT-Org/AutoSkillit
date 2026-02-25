"""Recipe discovery from .autoskillit/recipes/."""

from __future__ import annotations

from pathlib import Path

import yaml

from autoskillit import recipe_parser
from autoskillit._logging import get_logger
from autoskillit.config import load_config
from autoskillit.recipe_parser import RecipeInfo, RecipeSource
from autoskillit.sync_manifest import (
    SyncDecisionStore,
    SyncManifest,
    compute_recipe_hash,
    default_decision_path,
    default_manifest_path,
)
from autoskillit.types import LoadReport, LoadResult

logger = get_logger(__name__)


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
    """Sync bundled recipe YAMLs into .autoskillit/recipes/ with content-aware logic.

    Only runs if .autoskillit/ already exists in the project directory.
    Project-specific recipes with no bundled counterpart are left untouched.
    Locally modified recipes are preserved with a WARNING log.
    """
    autoskillit_dir = project_dir / ".autoskillit"
    if not autoskillit_dir.is_dir():
        return
    recipes_dir = autoskillit_dir / "recipes"
    recipes_dir.mkdir(exist_ok=True)
    bundled_dir = recipe_parser.builtin_recipes_dir()
    if not bundled_dir.is_dir():
        return

    config = load_config(project_dir)
    excluded = set(config.sync.excluded_recipes)
    manifest = SyncManifest(default_manifest_path(project_dir))

    for src in bundled_dir.glob("*.yaml"):
        recipe_name = src.stem
        if recipe_name in excluded:
            continue
        bundled_content = src.read_text()
        local_path = recipes_dir / src.name
        if not local_path.exists():
            local_path.write_text(bundled_content)
            manifest.record(recipe_name, bundled_content)
            continue
        local_content = local_path.read_text()
        bundled_hash = compute_recipe_hash(bundled_content)
        local_hash = compute_recipe_hash(local_content)
        manifest_hash = manifest.get_hash(recipe_name)
        is_unmodified = (local_hash == bundled_hash) or (
            manifest_hash is not None and local_hash == manifest_hash
        )
        if is_unmodified:
            if local_content != bundled_content:
                local_path.write_text(bundled_content)
                manifest.record(recipe_name, bundled_content)
        else:
            logger.warning("sync_skipped_modified_recipe", recipe_name=recipe_name)


def _get_pending_recipe_updates(project_dir: Path) -> list[str]:
    """Return bundled recipe names where a newer bundle is available but local copy is modified."""
    autoskillit_dir = project_dir / ".autoskillit"
    if not autoskillit_dir.is_dir():
        return []
    recipes_dir = autoskillit_dir / "recipes"
    config = load_config(project_dir)
    excluded = set(config.sync.excluded_recipes)
    manifest = SyncManifest(default_manifest_path(project_dir))
    decisions = SyncDecisionStore(default_decision_path(project_dir))
    bundled_dir = recipe_parser.builtin_recipes_dir()
    pending: list[str] = []
    for src in sorted(bundled_dir.glob("*.yaml")):
        recipe_name = src.stem
        if recipe_name in excluded:
            continue
        local_path = recipes_dir / src.name
        if not local_path.exists():
            continue
        bundled_content = src.read_text()
        local_content = local_path.read_text()
        bundled_hash = compute_recipe_hash(bundled_content)
        local_hash = compute_recipe_hash(local_content)
        if local_hash == bundled_hash:
            continue
        manifest_hash = manifest.get_hash(recipe_name)
        is_unmodified = manifest_hash is not None and local_hash == manifest_hash
        if is_unmodified:
            continue
        if decisions.is_declined(recipe_name, bundled_hash):
            continue
        pending.append(recipe_name)
    return pending
