"""Migration API: check_and_migrate convenience function for non-server contexts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.migration.engine import DefaultMigrationService, default_migration_engine


async def check_and_migrate(
    name: str,
    project_dir: Path,
) -> dict[str, Any]:
    """Check and apply pending migrations for a named recipe.

    Deterministic migrations (contract regeneration) are applied automatically.
    LLM-driven recipe migrations require a headless runner — if needed, returns
    an error directing the caller to use the migrate_recipe MCP tool.

    Returns:
        {"status": "up_to_date", "name": name}  — no migration needed
        {"status": "migrated", "name": name, ...}  — migration applied
        {"error": str, "name": name}            — failure or LLM needed
        {"error": str}                          — recipe not found
    """
    from autoskillit.recipe import find_recipe_by_name

    _pdir = project_dir if isinstance(project_dir, Path) else Path(project_dir)
    match = find_recipe_by_name(name, _pdir)
    if match is None:
        return {"error": f"No recipe named '{name}' found"}

    service = DefaultMigrationService(default_migration_engine())
    return await service.migrate(match.path)
