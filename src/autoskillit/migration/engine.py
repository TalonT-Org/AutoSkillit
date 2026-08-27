"""Migration engine core: dispatch logic, adapter ABCs, shared dataclasses,
factory, and private helpers used by the skill adapter.

Concrete adapters live in ``adapters_recipe.py``, ``adapters_contract.py``,
``adapters_diagram.py``, and ``adapters_skill.py``; the high-level service
wrapper lives in ``service.py``. This module re-exports every public symbol
and reassigns ``__module__`` so the relocated classes continue to report
``autoskillit.migration.engine`` as their home (preserving repr / pickle /
``inspect.getmodule`` identity for downstream consumers).
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS,
    SkillContractError,
    atomic_write,
    get_logger,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

_SKILL_SEARCH_DIR_PARTS: tuple[tuple[str, ...], ...] = tuple(
    Path(d).parts for d in ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
)


def _skill_project_dir(skill_md_path: Path) -> Path:
    """Derive the project root for a discovered project-local SKILL.md path.

    Path shape: ``<project_dir>/<search_dir>/<skill_name>/SKILL.md``, where
    ``search_dir`` is one of ``ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS``.
    """
    search_root = skill_md_path.parent.parent
    for parts in _SKILL_SEARCH_DIR_PARTS:
        if search_root.parts[-len(parts) :] == parts:
            return search_root.parents[len(parts) - 1]
    raise SkillContractError(
        f"{skill_md_path} is not under a recognized project-local skill search dir"
    )


def _normalize_legacy_child_spawn_cardinality(data: dict[str, Any]) -> str | None:
    """Preserve cardinalities accepted by the pre-explicit-authority parser."""
    requirements = data.get("semantic_requirements")
    if not isinstance(requirements, dict):
        return "semantic_requirements must be a mapping"
    raw_spawns = requirements.get("child_spawns")
    if not isinstance(raw_spawns, list):
        return "semantic_requirements.child_spawns must be a list"

    normalized: list[dict[str, Any]] = []
    for raw_spawn in raw_spawns:
        if not isinstance(raw_spawn, dict):
            return "semantic_requirements.child_spawns entries must be mappings"
        spawn = dict(raw_spawn)
        raw_count = spawn.get("count", 1)
        try:
            legacy_count = int(raw_count)
        except (TypeError, ValueError, OverflowError):
            return f"legacy child spawn count {raw_count!r} was not coercible to an integer"
        if legacy_count < 1:
            return f"legacy child spawn count {raw_count!r} was not positive"

        for_each = spawn.get("for_each")
        if for_each is not None:
            if not isinstance(for_each, str) or not for_each.strip():
                return "legacy child spawn for_each was not a non-empty string"
            if legacy_count != 1:
                return "legacy child spawn combined for_each with a non-default count"
            spawn.pop("count", None)
        else:
            spawn["count"] = legacy_count
        normalized.append(spawn)

    requirements = dict(requirements)
    requirements["child_spawns"] = normalized
    data["semantic_requirements"] = requirements
    return None


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
        run_headless: Callable[..., Awaitable[Any]],
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
        run_headless: Callable[..., Awaitable[Any]],
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

            # Late import: SkillMigrationAdapter.validate only runs after the
            # adapter has produced migrated content; importing at module load
            # time would create a circular dependency between engine.py and
            # adapters_skill.py.
            from autoskillit.migration.adapters_skill import SkillMigrationAdapter  # noqa: PLC0415

            if isinstance(adapter, SkillMigrationAdapter):
                is_valid, validation_error = adapter.validate(file.path)
                if not is_valid:
                    return MigrationResult(
                        success=False,
                        name=result.name,
                        error=f"post-migration validation failed: {validation_error}",
                        retries_attempted=result.retries_attempted,
                        advisory=result.advisory,
                    )

        return result


def default_migration_engine() -> MigrationEngine:
    """Create a MigrationEngine with all bundled adapters registered."""
    from autoskillit.migration.adapters_contract import ContractMigrationAdapter
    from autoskillit.migration.adapters_diagram import DiagramMigrationAdapter
    from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
    from autoskillit.migration.adapters_skill import SkillMigrationAdapter

    return MigrationEngine(
        [
            RecipeMigrationAdapter(),
            ContractMigrationAdapter(),
            DiagramMigrationAdapter(),
            SkillMigrationAdapter(),
        ]
    )


# ---------------------------------------------------------------------------
# Public-symbol re-exports for relocated classes. The class objects continue
# to be importable as ``autoskillit.migration.engine.<Name>`` and we reassign
# ``__module__`` so repr / pickle / inspect.getmodule continue to report
# ``autoskillit.migration.engine`` as the class's home.
# ---------------------------------------------------------------------------
from autoskillit.migration.adapters_contract import ContractMigrationAdapter  # noqa: E402
from autoskillit.migration.adapters_diagram import (  # noqa: E402
    AdvisoryResult,
    DiagramMigrationAdapter,
)
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter  # noqa: E402
from autoskillit.migration.adapters_skill import SkillMigrationAdapter  # noqa: E402
from autoskillit.migration.service import DefaultMigrationService  # noqa: E402

for _cls in (
    RecipeMigrationAdapter,
    ContractMigrationAdapter,
    DiagramMigrationAdapter,
    SkillMigrationAdapter,
    AdvisoryResult,
    DefaultMigrationService,
):
    _cls.__module__ = __name__

__all__ = [
    "AdvisoryMigrationAdapter",
    "AdvisoryResult",
    "ContractMigrationAdapter",
    "DefaultMigrationService",
    "DeterministicMigrationAdapter",
    "DiagramMigrationAdapter",
    "HeadlessMigrationAdapter",
    "MIGRATE_RECIPES_MAX_RETRIES",
    "MigrationAdapter",
    "MigrationEngine",
    "MigrationFile",
    "MigrationResult",
    "RecipeMigrationAdapter",
    "SkillMigrationAdapter",
    "default_migration_engine",
]
