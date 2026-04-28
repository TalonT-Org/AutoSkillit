from __future__ import annotations

import json
import re
import shutil
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit import __version__
from autoskillit.core import (
    RetryReason,
    SkillResult,
    atomic_write,
    dump_yaml_str,
    get_logger,
    load_yaml,
    resolve_temp_dir,
)
from autoskillit.migration.loader import applicable_migrations

logger = get_logger(__name__)

MIGRATE_RECIPES_MAX_RETRIES: int = 3
"""Max validation-retry attempts for LLM-driven recipe migration (matches SKILL.md)."""


@dataclass
class MigrationFile:
    name: str  # recipe or contract stem
    path: Path  # absolute path to the file
    file_type: str  # "recipe" or "contract"
    current_version: str | None


@dataclass
class MigrationResult:
    success: bool
    name: str
    migrated_content: str | None = None
    error: str | None = None
    retries_attempted: int = 0
    advisory: str | None = None


@dataclass
class AdvisoryResult:
    name: str
    stale: bool
    suggestion: str


class MigrationAdapter(ABC):
    """Abstract base for file-type-specific migration adapters."""

    file_type: str

    @abstractmethod
    def discover(self, project_dir: Path) -> list[MigrationFile]:
        """Discover all files of this type in the project."""

    @abstractmethod
    def needs_migration(self, file: MigrationFile) -> bool:
        """Return True if this file requires migration."""

    @abstractmethod
    def validate(self, path: Path) -> tuple[bool, str]:
        """Return (is_valid, error_message). Called after write-back."""


class HeadlessMigrationAdapter(MigrationAdapter):
    """Adapter that uses a headless Claude session for LLM-driven migration."""

    @abstractmethod
    async def migrate(
        self,
        file: MigrationFile,
        *,
        run_headless: Callable[..., Awaitable[SkillResult]],
        temp_dir: Path,
    ) -> MigrationResult:
        """Apply migration via run_headless; write-back handled by MigrationEngine."""


class DeterministicMigrationAdapter(MigrationAdapter):
    """Adapter that uses deterministic (non-LLM) migration logic."""

    @abstractmethod
    async def migrate(
        self,
        file: MigrationFile,
        *,
        temp_dir: Path,
    ) -> MigrationResult:
        """Apply migration deterministically; write-back handled by MigrationEngine."""


class AdvisoryMigrationAdapter(MigrationAdapter):
    """Adapter for skill-crafted artifacts: detects staleness but never writes files.

    Returns advisory results (warnings/suggestions) that surface in migration
    reports. File regeneration is deferred to the appropriate skill invocation.
    """

    @abstractmethod
    def check_staleness(self, file: MigrationFile) -> AdvisoryResult: ...


_AnyAdapter = HeadlessMigrationAdapter | DeterministicMigrationAdapter | AdvisoryMigrationAdapter


class MigrationEngine:
    def __init__(self, adapters: list[_AnyAdapter]) -> None:
        self._adapters: dict[str, _AnyAdapter] = {a.file_type: a for a in adapters}

    def get_adapter(self, file_type: str) -> _AnyAdapter | None:
        return self._adapters.get(file_type)

    async def migrate_file(
        self,
        file: MigrationFile,
        *,
        run_headless: Callable[..., Awaitable[SkillResult]],
        temp_dir: Path,
    ) -> MigrationResult:
        adapter = self._adapters.get(file.file_type)
        if adapter is None:
            return MigrationResult(
                success=False,
                name=file.name,
                error=f"No adapter registered for file type '{file.file_type}'",
            )
        if not adapter.needs_migration(file):
            return MigrationResult(success=True, name=file.name)

        if isinstance(adapter, AdvisoryMigrationAdapter):
            advisory = adapter.check_staleness(file)
            return MigrationResult(success=True, name=file.name, advisory=advisory.suggestion)
        elif isinstance(adapter, DeterministicMigrationAdapter):
            result = await adapter.migrate(file, temp_dir=temp_dir)
        else:
            result = await adapter.migrate(file, run_headless=run_headless, temp_dir=temp_dir)

        if not result.success:
            return result

        # Write migrated content back to original file
        if result.migrated_content is not None:
            shutil.copy2(file.path, file.path.with_suffix(".yaml.bak"))
            atomic_write(file.path, result.migrated_content)
            logger.info("migration.written_back", name=file.name, path=str(file.path))

        return result


class RecipeMigrationAdapter(HeadlessMigrationAdapter):
    file_type = "recipe"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        from autoskillit.recipe import parse_recipe_metadata

        recipes_dir = project_dir / ".autoskillit" / "recipes"
        if not recipes_dir.exists():
            return []
        files = []
        for p in sorted(recipes_dir.glob("*.yaml")):
            meta = parse_recipe_metadata(p)
            files.append(
                MigrationFile(
                    name=meta.name,
                    path=p,
                    file_type=self.file_type,
                    current_version=meta.version,
                )
            )
        return files

    def needs_migration(self, file: MigrationFile) -> bool:
        return bool(applicable_migrations(file.current_version, __version__))

    async def migrate(
        self,
        file: MigrationFile,
        *,
        run_headless: Callable[..., Awaitable[SkillResult]],
        temp_dir: Path,
    ) -> MigrationResult:
        migrations = applicable_migrations(file.current_version, __version__)
        if not migrations:
            return MigrationResult(success=True, name=file.name)

        notes_yaml = dump_yaml_str(
            [
                {
                    "from_version": m.from_version,
                    "to_version": m.to_version,
                    "description": m.description,
                    "changes": [
                        {
                            "id": c.id,
                            "description": c.description,
                            "instruction": c.instruction,
                            "detect": c.detect,
                            "example_before": c.example_before,
                            "example_after": c.example_after,
                        }
                        for c in m.changes
                    ],
                }
                for m in migrations
            ]
        )
        target_version = migrations[-1].to_version
        content = file.path.read_text()
        skill_command = (
            f"/autoskillit:migrate-recipes"
            f" script_path={file.path}"
            f" script_content={json.dumps(content)}"
            f" migration_notes={json.dumps(notes_yaml)}"
            f" target_version={target_version}"
        )

        raw = await run_headless(
            skill_command=skill_command,
            cwd=str(file.path.parent.parent.parent),
        )
        if not raw.success:
            return MigrationResult(
                success=False,
                name=file.name,
                error=raw.result or "headless session failed",
                retries_attempted=MIGRATE_RECIPES_MAX_RETRIES,
            )

        temp_out = self.get_temp_output_path(file, temp_dir)
        if not temp_out.exists():
            return MigrationResult(
                success=False,
                name=file.name,
                error="migrate-recipes did not produce output",
            )

        return MigrationResult(
            success=True,
            name=file.name,
            migrated_content=temp_out.read_text(),
        )

    def get_temp_output_path(self, file: MigrationFile, temp_dir: Path) -> Path:
        return temp_dir / "migrations" / f"{file.path.stem}.yaml"

    def validate(self, path: Path) -> tuple[bool, str]:
        from autoskillit.recipe import load_recipe as _parse_recipe
        from autoskillit.recipe import validate_recipe

        try:
            recipe = _parse_recipe(path)
            errors = validate_recipe(recipe)
            if errors:
                return False, "; ".join(str(e) for e in errors)
            return True, ""
        except Exception as exc:
            logger.warning("Recipe file validation failed", path=str(path), error=str(exc))
            return False, str(exc)


class ContractMigrationAdapter(DeterministicMigrationAdapter):
    file_type = "contract"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        contracts_dir = project_dir / ".autoskillit" / "recipes" / "contracts"
        if not contracts_dir.exists():
            return []
        files = []
        for p in sorted(contracts_dir.glob("*.yaml")):
            files.append(
                MigrationFile(
                    name=p.stem,
                    path=p,
                    file_type=self.file_type,
                    current_version=None,  # version tracked via staleness, not semver
                )
            )
        return files

    def needs_migration(self, file: MigrationFile) -> bool:
        from autoskillit.recipe import check_contract_staleness, load_recipe_card

        recipes_dir = file.path.parent.parent
        contract = load_recipe_card(file.name, recipes_dir)
        if contract is None:
            return True
        return bool(check_contract_staleness(contract))

    async def migrate(
        self,
        file: MigrationFile,
        *,
        temp_dir: Path,
    ) -> MigrationResult:
        from autoskillit.recipe import generate_recipe_card

        recipes_dir = file.path.parent.parent
        recipe_path = recipes_dir / f"{file.name}.yaml"
        if not recipe_path.exists():
            return MigrationResult(
                success=False,
                name=file.name,
                error=f"Source recipe '{file.name}.yaml' not found",
            )
        try:
            _ = generate_recipe_card(recipe_path, recipes_dir)
            return MigrationResult(success=True, name=file.name)
        except Exception as exc:
            logger.warning("Contract card generation failed", name=file.name, error=str(exc))
            return MigrationResult(success=False, name=file.name, error=str(exc))

    def validate(self, path: Path) -> tuple[bool, str]:
        try:
            data = load_yaml(path)
            if not isinstance(data, dict) or "skill_hashes" not in data:
                return False, "missing skill_hashes field"
            return True, ""
        except Exception as exc:
            logger.warning("Contract file validation failed", path=str(path), error=str(exc))
            return False, str(exc)


class DiagramMigrationAdapter(AdvisoryMigrationAdapter):
    """Advisory adapter for skill-crafted recipe flow diagrams.

    Detects stale diagrams but never overwrites them — returns a suggestion
    to run ``/render-recipe`` instead.
    """

    file_type = "diagram"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        diagrams_dir = project_dir / ".autoskillit" / "recipes" / "diagrams"
        if not diagrams_dir.is_dir():
            return []
        return [
            MigrationFile(name=p.stem, path=p, file_type="diagram", current_version=None)
            for p in sorted(diagrams_dir.glob("*.md"))
        ]

    def needs_migration(self, file: MigrationFile) -> bool:
        from autoskillit.recipe import check_diagram_staleness

        recipes_dir = file.path.parent.parent
        recipe_path = recipes_dir / f"{file.name}.yaml"
        if not recipe_path.exists():
            return False
        return check_diagram_staleness(file.name, recipes_dir, recipe_path)

    def check_staleness(self, file: MigrationFile) -> AdvisoryResult:
        from autoskillit.recipe import diagram_stale_to_suggestions

        suggestions = diagram_stale_to_suggestions(file.name)
        return AdvisoryResult(
            name=file.name,
            stale=True,
            suggestion=suggestions[0]["message"] if suggestions else "",
        )

    def validate(self, path: Path) -> tuple[bool, str]:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, str(exc)
        if not re.search(r"<!-- autoskillit-recipe-hash: sha256:[0-9a-f]+ -->", content):
            return False, "missing autoskillit-recipe-hash comment"
        return True, ""


def default_migration_engine() -> MigrationEngine:
    """Create a MigrationEngine with all bundled adapters registered."""
    return MigrationEngine(
        [RecipeMigrationAdapter(), ContractMigrationAdapter(), DiagramMigrationAdapter()]
    )


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
        from autoskillit.recipe import parse_recipe_metadata

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
