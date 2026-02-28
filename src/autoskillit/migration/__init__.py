"""L2 migration domain — version graph, adapter dispatch, failure persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.migration.engine import (
    DefaultMigrationService,
    MigrationEngine,
    MigrationFile,
    default_migration_engine,
)
from autoskillit.migration.loader import applicable_migrations, list_migrations
from autoskillit.migration.store import FailureStore, default_store_path

__all__ = [
    "MigrationEngine",
    "MigrationFile",
    "DefaultMigrationService",
    "default_migration_engine",
    "applicable_migrations",
    "list_migrations",
    "FailureStore",
    "default_store_path",
    "check_and_migrate",
]


async def check_and_migrate(
    name: str,
    project_dir: Path,
    installed_version: str,
) -> dict[str, Any]:
    """Check and apply pending migrations for a named recipe.

    Deterministic migrations (contract regeneration) are applied automatically.
    LLM-driven recipe migrations require a headless runner — if needed, returns
    an error directing the caller to use the migrate_recipe MCP tool.

    Does not require a headless runner to be passed at call time.

    Returns:
        {"status": "up_to_date", "name": name}  — no migration needed
        {"status": "migrated", "name": name}    — migration applied
        {"error": str, "name": name}            — failure or LLM needed
        {"error": str}                          — recipe not found
    """
    from pathlib import Path as _Path
    from typing import Any as _Any

    from autoskillit.core import RetryReason, SkillResult
    from autoskillit.migration.engine import MigrationFile as _MigFile
    from autoskillit.migration.engine import default_migration_engine as _engine_factory
    from autoskillit.migration.loader import applicable_migrations as _applicable
    from autoskillit.recipe import find_recipe_by_name
    from autoskillit.recipe import load_recipe as _parse

    _pdir = project_dir if isinstance(project_dir, _Path) else _Path(project_dir)
    match = find_recipe_by_name(name, _pdir)
    if match is None:
        return {"error": f"No recipe named '{name}' found"}

    recipe = _parse(match.path)
    migrations = _applicable(recipe.version, installed_version)
    if not migrations:
        return {"status": "up_to_date", "name": name}

    temp_dir = _pdir / ".autoskillit" / "temp"
    engine = _engine_factory()
    file = _MigFile(
        name=name,
        path=match.path,
        file_type="recipe",
        current_version=recipe.version,
    )

    async def _no_headless(*args: _Any, **kwargs: _Any) -> SkillResult:
        return SkillResult(
            success=False,
            result=(
                "LLM-driven migration requires a headless runner. "
                "Use the migrate_recipe MCP tool instead."
            ),
            session_id="",
            subtype="no_runner",
            is_error=True,
            exit_code=1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            token_usage=None,
        )

    result = await engine.migrate_file(file, run_headless=_no_headless, temp_dir=temp_dir)
    if result.success:
        return {"status": "migrated", "name": name}
    return {"error": result.error or "Migration failed", "name": name}
