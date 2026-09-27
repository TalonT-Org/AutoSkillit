"""Typed authority for AutoSkillit's process home."""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from pathlib import Path

__all__ = ["ManagedHome", "managed_home", "managed_home_for"]


_MANAGED_HOME_FACTORY_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ManagedHome:
    """The single root under which AutoSkillit's own state is read and written.

    Cannot be constructed directly — use :func:`managed_home` or
    :func:`managed_home_for`.
    """

    root: Path
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _MANAGED_HOME_FACTORY_TOKEN:
            raise ValueError("ManagedHome must be built by managed_home()/managed_home_for()")
        if not isinstance(self.root, Path):
            raise TypeError("managed home root must be a Path")
        if not self.root.is_absolute():
            raise ValueError(f"managed home must be absolute: {self.root}")

    @property
    def autoskillit_dir(self) -> Path:
        return self.root / ".autoskillit"

    def contains(self, path: Path) -> bool:
        """Return whether *path* resolves to this home or one of its descendants."""
        try:
            root = self.root.resolve(strict=False)
            location = Path(path).resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        return location == root or location.is_relative_to(root)

    def __fspath__(self) -> str:
        return str(self.root)


def managed_home() -> ManagedHome:
    """Resolve the process's managed home from :meth:`pathlib.Path.home`."""
    return managed_home_for(Path.home())


def managed_home_for(root: Path) -> ManagedHome:
    """Bind an explicit managed home — the injectable form for tests and tools."""
    return ManagedHome(root=Path(root), _factory_token=_MANAGED_HOME_FACTORY_TOKEN)
