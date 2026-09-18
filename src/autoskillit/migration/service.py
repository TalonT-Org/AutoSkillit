"""Default migration service and the composition root that wires it.

``DefaultMigrationService`` wraps ``MigrationEngine.migrate_file`` with
recipe+contract+diagram orchestration, ``FailureStore`` recording, and the
LLM-required fallback when ``run_headless`` is not wired in.

``default_migration_engine`` lives here rather than in ``engine.py`` so the
engine stays a leaf: the adapters import the ABCs from ``engine``, and only
this module imports the adapters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict, cast

from autoskillit import __version__
from autoskillit.core import RetryReason, SkillResult, get_logger, resolve_temp_dir
from autoskillit.migration.adapters_contract import ContractMigrationAdapter
from autoskillit.migration.adapters_diagram import DiagramMigrationAdapter
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
from autoskillit.migration.adapters_skill import SkillMigrationAdapter
from autoskillit.migration.engine import (
    HeadlessRunner,
    MigrationEngine,
    MigrationFile,
)
from autoskillit.migration.loader import applicable_migrations as _applicable

logger = get_logger(__name__)


async def _no_headless_runner(*args: Any, **kwargs: Any) -> SkillResult:
    """Return the normal engine result when no headless runner is wired in."""
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


async def _regenerate_contract_card(
    engine: MigrationEngine,
    *,
    name: str,
    recipes_dir: Path,
    run_headless: HeadlessRunner,
    temp_dir: Path,
) -> bool:
    """Regenerate a stale contract card and report whether it succeeded."""
    contract_adapter = engine.get_adapter("contract")
    if contract_adapter is None:
        return False

    contract_file = MigrationFile(
        name=name,
        path=recipes_dir / "contracts" / f"{name}.yaml",
        file_type="contract",
        current_version=None,
    )
    if not contract_adapter.needs_migration(contract_file):
        return False

    contract_result = await engine.migrate_file(
        contract_file,
        run_headless=run_headless,
        temp_dir=temp_dir,
    )
    if contract_result.success:
        return True

    logger.warning(
        "contract.migration_failed",
        name=name,
        error=contract_result.error,
    )
    return False


async def _get_diagram_advisory(
    engine: MigrationEngine,
    *,
    name: str,
    recipes_dir: Path,
    run_headless: HeadlessRunner,
    temp_dir: Path,
) -> str | None:
    """Return the advisory for a stale diagram without making it a migration."""
    diagram_adapter = engine.get_adapter("diagram")
    if diagram_adapter is None:
        return None

    diagram_file = MigrationFile(
        name=name,
        path=recipes_dir / "diagrams" / f"{name}.md",
        file_type="diagram",
        current_version=None,
    )
    if not diagram_adapter.needs_migration(diagram_file):
        return None

    diagram_result = await engine.migrate_file(
        diagram_file,
        run_headless=run_headless,
        temp_dir=temp_dir,
    )
    if diagram_result.advisory:
        return diagram_result.advisory
    if not diagram_result.success:
        logger.warning(
            "diagram.migration_failed",
            name=name,
            error=diagram_result.error,
        )
    return None


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
        run_headless: HeadlessRunner | None = None,
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

        run_headless = self._run_headless or _no_headless_runner

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

        contract_regenerated = await _regenerate_contract_card(
            self._engine,
            name=name,
            recipes_dir=recipes_dir,
            run_headless=run_headless,
            temp_dir=temp_dir,
        )
        diagram_advisory = await _get_diagram_advisory(
            self._engine,
            name=name,
            recipes_dir=recipes_dir,
            run_headless=run_headless,
            temp_dir=temp_dir,
        )
        contracts_regenerated = [name] if contract_regenerated else []
        advisories = [diagram_advisory] if diagram_advisory else []

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
