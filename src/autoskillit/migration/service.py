"""Default migration service and the composition root that wires it.

``DefaultMigrationService`` wraps ``MigrationEngine.migrate_file`` with
recipe+contract+diagram orchestration, ``FailureStore`` recording, and the
LLM-required fallback when ``run_headless`` is not wired in.

``default_migration_engine`` lives here rather than in ``engine.py`` so the
engine stays a leaf: the adapters import the ABCs from ``engine``, and only
this module imports the adapters.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypedDict, cast

from autoskillit import __version__
from autoskillit.core import RetryReason, SkillResult, get_logger, resolve_temp_dir
from autoskillit.migration.adapters_contract import ContractMigrationAdapter
from autoskillit.migration.adapters_diagram import DiagramMigrationAdapter
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
from autoskillit.migration.adapters_skill import SkillMigrationAdapter
from autoskillit.migration.engine import MigrationEngine, MigrationFile
from autoskillit.migration.loader import applicable_migrations as _applicable

logger = get_logger(__name__)


def default_migration_engine() -> MigrationEngine:
    """Create a MigrationEngine with all bundled adapters registered."""
    return MigrationEngine(
        [
            RecipeMigrationAdapter(),
            ContractMigrationAdapter(),
            DiagramMigrationAdapter(),
            SkillMigrationAdapter(),
        ]
    )


class MigrationServiceError(TypedDict):
    """Returned when migration fails before any work happens."""

    error: str
    name: str


class MigrationServiceUpToDate(TypedDict, total=False):
    """Returned when no migration was needed but advisories may still apply."""

    status: str  # always "up_to_date"
    name: str
    advisories: list[str]


class MigrationServiceMigrated(TypedDict, total=False):
    """Returned when at least one migration or regeneration actually ran."""

    status: str  # always "migrated"
    name: str
    contracts_regenerated: list[str]
    advisories: list[str]


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

        Returns one of three TypedDict shapes:

        - ``MigrationServiceError`` — ``{"error": str, "name": str}`` when the
          migration fails before any work happens.
        - ``MigrationServiceUpToDate`` — ``{"status": "up_to_date", "name": str,
          "advisories"?: list[str]}`` when no migration was needed (advisories
          may still surface).
        - ``MigrationServiceMigrated`` — ``{"status": "migrated", "name": str,
          "contracts_regenerated": list[str], "advisories"?: list[str]}`` when
          at least one migration or regeneration actually ran.
        """
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
            run_headless: Callable[..., Awaitable[SkillResult]] = self._run_headless
        else:

            async def run_headless(*args: Any, **kwargs: Any) -> SkillResult:  # type: ignore[misc]
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
                return cast(
                    dict[str, Any],
                    MigrationServiceError(
                        error=f"Migration failed: {migration_result.error}",
                        name=name,
                    ),
                )

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
            result: MigrationServiceMigrated = {
                "status": "migrated",
                "name": name,
                "contracts_regenerated": contracts_regenerated,
            }
            if advisories:
                result["advisories"] = advisories
            return cast(dict[str, Any], result)
        up_to_date: MigrationServiceUpToDate = {"status": "up_to_date", "name": name}
        if advisories:
            up_to_date["advisories"] = advisories
        return cast(dict[str, Any], up_to_date)
