"""Workspace and skill management protocol definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._type_results import CleanupResult, CloneResult, ValidatedAddDir

__all__ = [
    "WorkspaceManager",
    "CloneManager",
    "SessionSkillManager",
    "SkillLister",
    "SkillResolver",
]


@runtime_checkable
class WorkspaceManager(Protocol):
    """Protocol for directory teardown operations."""

    def delete_contents(
        self,
        directory: Path,
        preserve: set[str] | None = None,
    ) -> CleanupResult: ...


@runtime_checkable
class CloneManager(Protocol):
    """Protocol for clone-based pipeline run isolation."""

    def clone_repo(
        self,
        source_dir: str,
        run_name: str,
        branch: str = "",
        strategy: str = "",
        remote_url: str = "",
    ) -> CloneResult: ...

    def remove_clone(self, clone_path: str, keep: str = "false") -> dict[str, str]: ...

    def push_to_remote(
        self,
        clone_path: str,
        source_dir: str = "",
        branch: str = "",
        *,
        remote_url: str = "",
        protected_branches: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, str | bool]: ...


@runtime_checkable
class SessionSkillManager(Protocol):
    """Protocol for managing per-session ephemeral skill directories."""

    def init_session(
        self,
        session_id: str,
        *,
        cook_session: bool = False,
        config: Any | None = None,
        project_dir: Path | None = None,
        recipe_packs: frozenset[str] | None = None,
        allow_only: frozenset[str] | None = None,
    ) -> ValidatedAddDir: ...

    def compute_skill_closure(self, skill_name: str) -> frozenset[str]: ...

    def activate_skill_deps(self, session_id: str, skill_name: str) -> bool: ...

    def cleanup_session(self, session_id: str) -> bool: ...

    def cleanup_stale(self, max_age_seconds: int = 86400) -> int: ...


@runtime_checkable
class SkillResolver(Protocol):
    """Protocol for resolving skill names to their source tier."""

    def resolve(self, name: str) -> Any: ...


@runtime_checkable
class SkillLister(Protocol):
    """IL-0 contract for listing all available skills.

    Allows IL-2 recipe rules to type their skill-listing dependency
    against an IL-0 protocol instead of binding to the IL-1 workspace
    concrete class. The default implementation lives at
    autoskillit.workspace.skills.DefaultSkillResolver and satisfies this
    protocol structurally.
    """

    def list_all(self) -> list[Any]: ...
