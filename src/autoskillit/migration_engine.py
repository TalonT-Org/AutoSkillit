from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml as _yaml

from autoskillit import __version__
from autoskillit._io import _atomic_write
from autoskillit._logging import get_logger
from autoskillit.migration_loader import applicable_migrations
from autoskillit.recipe_loader import _parse_recipe_metadata
from autoskillit.recipe_parser import load_recipe as _parse_recipe
from autoskillit.recipe_parser import validate_recipe

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


class MigrationAdapter(Protocol):
    """Contract for file-type-specific migration adapters."""

    file_type: str

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        """Discover all files of this type in the project."""
        ...

    def needs_migration(self, file: MigrationFile) -> bool:
        """Return True if this file requires migration."""
        ...

    async def migrate(
        self,
        file: MigrationFile,
        *,
        run_headless: Callable[..., Awaitable[dict]],
        temp_dir: Path,
    ) -> MigrationResult:
        """Apply migration and return the result. Write-back handled by MigrationEngine."""
        ...

    def validate(self, path: Path) -> tuple[bool, str]:
        """Return (is_valid, error_message). Called after write-back."""
        ...


class MigrationEngine:
    def __init__(self, adapters: list[MigrationAdapter]) -> None:
        self._adapters: dict[str, MigrationAdapter] = {a.file_type: a for a in adapters}

    def get_adapter(self, file_type: str) -> MigrationAdapter | None:
        return self._adapters.get(file_type)

    async def migrate_file(
        self,
        file: MigrationFile,
        *,
        run_headless: Callable[..., Awaitable[dict]],
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

        result = await adapter.migrate(file, run_headless=run_headless, temp_dir=temp_dir)

        if not result.success:
            return result

        # Write migrated content back to original file
        if result.migrated_content is not None:
            shutil.copy2(file.path, file.path.with_suffix(".yaml.bak"))
            _atomic_write(file.path, result.migrated_content)
            logger.info("migration.written_back", name=file.name, path=str(file.path))

        return result


class RecipeMigrationAdapter:
    file_type = "recipe"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        recipes_dir = project_dir / ".autoskillit" / "recipes"
        if not recipes_dir.exists():
            return []
        files = []
        for p in sorted(recipes_dir.glob("*.yaml")):
            meta = _parse_recipe_metadata(p)
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
        run_headless: Callable[..., Awaitable[dict]],
        temp_dir: Path,
    ) -> MigrationResult:
        migrations = applicable_migrations(file.current_version, __version__)
        if not migrations:
            return MigrationResult(success=True, name=file.name)

        notes_yaml = _yaml.dump(
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
        if not raw.get("success"):
            return MigrationResult(
                success=False,
                name=file.name,
                error=raw.get("result", "headless session failed"),
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
        try:
            recipe = _parse_recipe(path)
            errors = validate_recipe(recipe)
            if errors:
                return False, "; ".join(str(e) for e in errors)
            return True, ""
        except Exception as exc:
            logger.warning("Recipe file validation failed", path=str(path), error=str(exc))
            return False, str(exc)


class ContractMigrationAdapter:
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
        from autoskillit.contract_validator import check_contract_staleness, load_recipe_card

        recipes_dir = file.path.parent.parent
        contract = load_recipe_card(file.name, recipes_dir)
        if contract is None:
            return True
        return bool(check_contract_staleness(contract))

    async def migrate(
        self,
        file: MigrationFile,
        *,
        run_headless: Callable[..., Awaitable[dict]],  # unused — deterministic regeneration
        temp_dir: Path,
    ) -> MigrationResult:
        from autoskillit.contract_validator import generate_recipe_card

        recipes_dir = file.path.parent.parent
        recipe_path = recipes_dir / f"{file.name}.yaml"
        if not recipe_path.exists():
            return MigrationResult(
                success=False,
                name=file.name,
                error=f"Source recipe '{file.name}.yaml' not found",
            )
        try:
            generate_recipe_card(recipe_path, recipes_dir)
            return MigrationResult(success=True, name=file.name)
        except Exception as exc:
            logger.warning("Contract card generation failed", name=file.name, error=str(exc))
            return MigrationResult(success=False, name=file.name, error=str(exc))

    def validate(self, path: Path) -> tuple[bool, str]:
        try:
            import yaml as _y

            data = _y.safe_load(path.read_text())
            if not isinstance(data, dict) or "skill_hashes" not in data:
                return False, "missing skill_hashes field"
            return True, ""
        except Exception as exc:
            logger.warning("Contract file validation failed", path=str(path), error=str(exc))
            return False, str(exc)


def default_migration_engine() -> MigrationEngine:
    """Create a MigrationEngine with all bundled adapters registered."""
    return MigrationEngine([RecipeMigrationAdapter(), ContractMigrationAdapter()])
