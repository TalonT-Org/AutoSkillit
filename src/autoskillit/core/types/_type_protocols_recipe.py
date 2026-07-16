"""Recipe and data access protocol definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, KeysView, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe, RecipeInfo

    from ._type_backend import BackendCapabilities
    from ._type_protocols_logging import SupportsDebug
else:
    # Re-exported at runtime so typing.get_type_hints() can resolve the string
    # annotations in the RecipeRepository protocol below (from __future__ import
    # annotations defers everything to strings).
    from ._type_backend import BackendCapabilities  # noqa: F401

from ._type_results import LoadResult

__all__ = [
    "RecipeRepository",
    "MigrationService",
    "DatabaseReader",
    "ReadOnlyResolver",
    "ServeOverridesSnapshot",
]


@runtime_checkable
class ServeOverridesSnapshot(Protocol):
    """Caller-supplied ingredient override values captured at open_kitchen time.

    Always stored as a plain dict[str, str]. Protocol type enables the
    test_toolcontext_optional_fields_all_have_protocol_annotations arch contract
    while remaining compatible with dict[str, str] at all call sites.
    """

    def __getitem__(self, __key: str) -> str: ...
    def keys(self) -> KeysView[str]: ...
    def __iter__(self) -> Iterator[str]: ...
    def __len__(self) -> int: ...


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
        defer_unresolved: bool = False,
        backend_name: str | None = None,
        effective_backend_map: dict[str, str] | None = None,
        backend_capabilities_map: dict[str, BackendCapabilities] | None = None,
    ) -> dict[str, Any]:
        """Load and validate a recipe.

        Raises ProcessStaleError or RecipeNotFoundError on failure.
        """
        ...

    def validate_from_path(
        self,
        script_path: Any,
        temp_dir_relpath: str = ".autoskillit/temp",
        *,
        backend_name: str | None = None,
        ingredient_overrides: dict[str, str] | None = None,
        effective_backend_map: dict[str, str] | None = None,
        backend_capabilities_map: dict[str, BackendCapabilities] | None = None,
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
