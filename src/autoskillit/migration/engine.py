"""Migration engine core: dispatch logic, adapter ABCs, and shared dataclasses.

``MigrationEngine`` routes a ``MigrationFile`` to the adapter registered for
its ``file_type``, writes migrated content back atomically, and runs the
adapter's optional post-write revalidation.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    SkillResult,
    atomic_write,
    get_logger,
)

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

    def post_migration_validate(self, path: Path) -> tuple[bool, str] | None:
        """Run a typed re-validation after the engine writes migrated content.

        Override to opt in to a strict check on the file's contents. Returning
        ``None`` (the default) tells the engine the adapter has no extra
        check beyond the structural ``validate`` already exposed.
        """

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

            post_check = adapter.post_migration_validate(file.path)
            if post_check is not None:
                is_valid, validation_error = post_check
                if not is_valid:
                    return MigrationResult(
                        success=False,
                        name=result.name,
                        error=f"post-migration validation failed: {validation_error}",
                        retries_attempted=result.retries_attempted,
                        advisory=result.advisory,
                    )

        return result


__all__ = [
    "AdvisoryMigrationAdapter",
    "AdvisoryResult",
    "DeterministicMigrationAdapter",
    "HeadlessMigrationAdapter",
    "MIGRATE_RECIPES_MAX_RETRIES",
    "MigrationAdapter",
    "MigrationEngine",
    "MigrationFile",
    "MigrationResult",
]
