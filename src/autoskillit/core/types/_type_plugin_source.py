"""Import-layer-safe plugin artifact lifecycle value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

__all__ = [
    "DirectInstall",
    "PluginArtifactIdentity",
    "PluginLaunchBinding",
    "PluginLoadMode",
]


class _LeaseOwner(Protocol):
    @property
    def closed(self) -> bool: ...

    def close(self) -> None: ...


class PluginLoadMode(StrEnum):
    """How a selected backend consumes a plugin artifact for one launch."""

    EXPLICIT_PLUGIN_DIR = "explicit_plugin_dir"
    PROJECTED_HOME = "projected_home"
    GENERATED_HOME = "generated_home"
    IMPLICIT_INSTALLED = "implicit_installed"
    NONE = "none"

    @property
    def consumes_artifact(self) -> bool:
        return self in {
            PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            PluginLoadMode.PROJECTED_HOME,
            PluginLoadMode.IMPLICIT_INSTALLED,
        }


@dataclass(frozen=True, slots=True)
class PluginArtifactIdentity:
    """Exact identity and validation evidence for one physical incarnation."""

    semantic_key: str
    incarnation_id: str
    manifest_schema_version: int
    artifact_digest: str
    managed_path: Path
    manifest_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "managed_path", Path(self.managed_path))
        object.__setattr__(self, "manifest_path", Path(self.manifest_path))
        if not self.semantic_key:
            raise ValueError("plugin artifact semantic_key must not be empty")
        if not self.incarnation_id:
            raise ValueError("plugin artifact incarnation_id must not be empty")
        if self.manifest_schema_version < 1:
            raise ValueError("plugin artifact manifest schema version must be positive")
        if not self.artifact_digest:
            raise ValueError("plugin artifact digest must not be empty")
        if not self.managed_path.is_absolute():
            raise ValueError(f"plugin artifact managed path must be absolute: {self.managed_path}")
        if not self.manifest_path.is_absolute():
            raise ValueError(
                f"plugin artifact manifest path must be absolute: {self.manifest_path}"
            )


@dataclass(frozen=True, slots=True)
class PluginLaunchBinding:
    """One launch's exact artifact path and inherited reader ownership."""

    load_mode: PluginLoadMode
    plugin_dir: Path | None
    identity: PluginArtifactIdentity
    inherited_fds: tuple[int, ...]
    _lease: _LeaseOwner

    def __post_init__(self) -> None:
        if not self.load_mode.consumes_artifact:
            raise ValueError(
                f"plugin launch binding cannot use non-artifact mode {self.load_mode.value!r}"
            )
        if self.plugin_dir is not None:
            object.__setattr__(self, "plugin_dir", Path(self.plugin_dir))
            if not self.plugin_dir.is_absolute():
                raise ValueError(f"plugin launch path must be absolute: {self.plugin_dir}")
        if self.load_mode is not PluginLoadMode.IMPLICIT_INSTALLED and self.plugin_dir is None:
            raise ValueError(f"{self.load_mode.value} requires a plugin path")
        normalized_fds = tuple(dict.fromkeys(self.inherited_fds))
        if any(fd < 0 for fd in normalized_fds):
            raise ValueError("plugin launch inherited descriptors must be non-negative")
        object.__setattr__(self, "inherited_fds", normalized_fds)

    @property
    def closed(self) -> bool:
        return self._lease.closed

    def close(self) -> None:
        self._lease.close()

    def __enter__(self) -> PluginLaunchBinding:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class DirectInstall:
    """A raw plugin root — the projection input, never handed to a session."""

    plugin_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_dir", Path(self.plugin_dir))
