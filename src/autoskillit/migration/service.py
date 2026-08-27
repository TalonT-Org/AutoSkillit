"""Default migration service — wraps ``MigrationEngine.migrate_file`` with
recipe+contract+diagram orchestration, ``FailureStore`` recording, and the
LLM-required fallback when ``run_headless`` is not wired in.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from autoskillit import __version__
from autoskillit.core import RetryReason, SkillResult, get_logger, resolve_temp_dir
from autoskillit.migration.engine import MigrationEngine, MigrationFile

logger = get_logger(__name__)


class DefaultMigrationService:
    """Concrete MigrationService wrapping MigrationEngine.migrate_file.

    Pass run_headless at construction time to enable LLM-driven recipe migration.
    Without a headless runner, migrate() returns an error for recipes that require
    LLM-assisted migration.
    """

    def __init__(
        self,
        engine: MigrationEngine,
        *,
        run_headless: Callable[..., Awaitable[SkillResult]] | None = None,
        temp_dir: Path | None = None,
    ) -> None:
        self._engine = engine
        self._run_headless = run_headless
        self._temp_dir_override = temp_dir

    async def migrate(self, recipe_path: Path) -> dict[str, Any]:
        """Apply pending migration notes to the recipe file at recipe_path.

        Checks for applicable migrations, runs the migration engine (LLM-driven
        if a headless runner is wired in), handles FailureStore recording, and
        regenerates the contract card when stale.

        Returns a dict with:
          {"status": "up_to_date", "name": name}  — no migration needed
          {"status": "migrated", "name": name, "contracts_regenerated": [...]}
              — version migration applied and/or stale contracts regenerated
          {"error": str, "name": name}             — migration failed
        """
        from autoskillit.migration.loader import applicable_migrations as _applicable
        from autoskillit.migration.store import FailureStore, default_store_path
        from autoskillit.recipe import parse_recipe_metadata  # noqa: PLC0415

        meta = parse_recipe_metadata(recipe_path)
        name = meta.name
        migrations = _applicable(meta.version, __version__)

        # Derive project_dir: recipe_path → recipes_dir → .autoskillit/ → project_dir
        recipes_dir = recipe_path.parent
        project_dir = recipes_dir.parent.parent
        if self._temp_dir_override is not None:
            temp_dir = self._temp_dir_override
        else:
            temp_dir = resolve_temp_dir(project_dir, None)

        if self._run_headless is not None:
            run_headless: Callable[..., Awaitable[SkillResult]] = self._run_headless  # type: ignore[assignment,no-redef]
        else:

            async def run_headless(*args: Any, **kwargs: Any) -> SkillResult:  # type: ignore[no-redef,misc]
                return SkillResult(
                    success=False,
                    result=(
                        "LLM-driven migration requires a headless runner. "
                        "Use the migrate_recipe MCP tool directly."
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

        did_version_migrate = False
        if migrations:
            file = MigrationFile(
                name=name,
                path=recipe_path,
                file_type="recipe",
                current_version=meta.version,
            )

            migration_result = await self._engine.migrate_file(
                file, run_headless=run_headless, temp_dir=temp_dir
            )

            failure_store = FailureStore(default_store_path(project_dir, temp_dir=temp_dir))

            if migration_result.success:
                failure_store.clear(name)
                did_version_migrate = True
            else:
                failure_store.record(
                    name=name,
                    file_path=recipe_path,
                    file_type="recipe",
                    error=migration_result.error or "unknown",
                    retries_attempted=migration_result.retries_attempted,
                )
                return {"error": f"Migration failed: {migration_result.error}", "name": name}

        advisories: list[str] = []
        contracts_regenerated: list[str] = []
        contract_adapter = self._engine.get_adapter("contract")
        if contract_adapter is not None:
            contract_file = MigrationFile(
                name=name,
                path=recipes_dir / "contracts" / f"{name}.yaml",
                file_type="contract",
                current_version=None,
            )
            if contract_adapter.needs_migration(contract_file):
                contract_result = await self._engine.migrate_file(
                    contract_file,
                    run_headless=run_headless,
                    temp_dir=temp_dir,
                )
                if contract_result.success:
                    contracts_regenerated.append(name)
                else:
                    logger.warning(
                        "contract.migration_failed",
                        name=name,
                        error=contract_result.error,
                    )

        diagram_adapter = self._engine.get_adapter("diagram")
        if diagram_adapter is not None:
            diagram_file = MigrationFile(
                name=name,
                path=recipes_dir / "diagrams" / f"{name}.md",
                file_type="diagram",
                current_version=None,
            )
            if diagram_adapter.needs_migration(diagram_file):
                diagram_result = await self._engine.migrate_file(
                    diagram_file,
                    run_headless=run_headless,
                    temp_dir=temp_dir,
                )
                if diagram_result.advisory:
                    advisories.append(diagram_result.advisory)
                elif not diagram_result.success:
                    logger.warning(
                        "diagram.migration_failed",
                        name=name,
                        error=diagram_result.error,
                    )

        if did_version_migrate or contracts_regenerated:
            result_dict: dict[str, object] = {
                "status": "migrated",
                "name": name,
                "contracts_regenerated": contracts_regenerated,
            }
            if advisories:
                result_dict["advisories"] = advisories
            return result_dict
        result_dict = {"status": "up_to_date", "name": name}
        if advisories:
            result_dict["advisories"] = advisories
        return result_dict
