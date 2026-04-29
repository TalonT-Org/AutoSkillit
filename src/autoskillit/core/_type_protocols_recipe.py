"""Recipe and data access protocol definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe, RecipeInfo

    from ._type_protocols_logging import SupportsDebug

from ._type_results import LoadResult

__all__ = ["RecipeRepository", "MigrationService", "DatabaseReader", "ReadOnlyResolver"]


@runtime_checkable
class RecipeRepository(Protocol):
    """Protocol for recipe discovery and loading."""

    def find(self, name: str, project_dir: Path) -> RecipeInfo | None: ...

    def load(self, path: Path) -> Recipe: ...

    def list(self, project_dir: Path) -> LoadResult[RecipeInfo]: ...

    def load_and_validate(
        self,
        name: str,
        project_dir: Path | str,
        *,
        suppressed: Sequence[str] | None = None,
        resolved_defaults: dict[str, str] | None = None,
        ingredient_overrides: dict[str, str] | None = None,
        temp_dir: Path | None = None,
        temp_dir_relpath: str | None = None,
    ) -> dict[str, Any]: ...

    def validate_from_path(
        self, script_path: Any, temp_dir_relpath: str = ".autoskillit/temp"
    ) -> dict[str, Any]: ...

    def list_all(
        self,
        project_dir: Any | None = None,
        *,
        features: dict[str, bool] | None = None,
    ) -> dict[str, Any]: ...

    async def apply_triage_gate(
        self,
        result: dict[str, Any],
        recipe_name: str,
        recipe_info: Any,
        temp_dir: Path,
        logger: SupportsDebug,
        triage_fn: Callable[..., Awaitable[Sequence[dict[str, Any]]]] | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class MigrationService(Protocol):
    """Protocol for applying migration notes to a recipe file."""

    async def migrate(self, recipe_path: Path) -> dict[str, Any]: ...


@runtime_checkable
class DatabaseReader(Protocol):
    """Protocol for read-only SQLite query execution."""

    def query(
        self,
        db_path: str,
        sql: str,
        params: list | dict,  # type: ignore[type-arg]  # sqlite3 cursor.execute accepts any sequence or mapping
        timeout_sec: int,
        max_rows: int,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ReadOnlyResolver(Protocol):
    """Protocol for resolving whether a skill is read-only from skill contracts."""

    def __call__(self, skill_command: str) -> bool: ...
