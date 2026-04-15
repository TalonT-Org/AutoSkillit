"""Migration API: top-level check_and_migrate convenience function.

Cross-L2 coupling note: migration/ (L2) imports from recipe/ (L2). This is
architecturally correct — migration inherently needs to discover, parse, and
validate recipes. The coupling is not extractable without inverting control in a
way that would complicate callers. The # noqa: PLC0415 comments on the deferred
recipe imports in this module and in engine.py exist to acknowledge this accepted
coupling explicitly rather than suppress the lint warning silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.core import RetryReason, SkillResult, resolve_temp_dir
from autoskillit.migration.engine import MigrationFile, default_migration_engine
from autoskillit.migration.loader import applicable_migrations


async def check_and_migrate(
    name: str,
    project_dir: Path,
    installed_version: str,
    temp_dir: Path | None = None,
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
    from autoskillit.recipe import find_recipe_by_name  # noqa: PLC0415
    from autoskillit.recipe import load_recipe as _parse  # noqa: PLC0415

    _pdir = project_dir if isinstance(project_dir, Path) else Path(project_dir)
    match = find_recipe_by_name(name, _pdir)
    if match is None:
        return {"error": f"No recipe named '{name}' found"}

    recipe = _parse(match.path)
    migrations = applicable_migrations(recipe.version, installed_version)
    if not migrations:
        return {"status": "up_to_date", "name": name}

    if temp_dir is None:
        temp_dir = resolve_temp_dir(_pdir, None)
    engine = default_migration_engine()
    file = MigrationFile(
        name=name,
        path=match.path,
        file_type="recipe",
        current_version=recipe.version,
    )

    async def _no_headless(*args: Any, **kwargs: Any) -> SkillResult:
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
