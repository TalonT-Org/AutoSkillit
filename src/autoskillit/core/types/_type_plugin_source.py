"""Import-layer-safe plugin artifact lifecycle value objects."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeGuard
from uuid import UUID, uuid4

__all__ = [
    "DirectInstall",
    "LegacyRetiringEvidence",
    "PluginArtifactKind",
    "PluginArtifactIdentity",
    "PluginLaunchBinding",
    "PluginLoadMode",
    "RetiringAppendResult",
    "RetiringArtifactRecord",
    "RetiringCacheReadResult",
    "RetiringCacheState",
    "RetirementOutcome",
    "is_canonical_plugin_artifact_digest",
    "is_canonical_plugin_artifact_incarnation_id",
    "new_plugin_artifact_incarnation_id",
    "normalize_inherited_fds",
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


class PluginArtifactKind(StrEnum):
    """Managed plugin artifact families sharing the retirement queue."""

    PROJECTION = "projection"
    INSTALLED_PLUGIN = "installed_plugin"


class RetiringCacheState(StrEnum):
    """Precisely classified persisted retirement-cache states."""

    ABSENT = "absent"
    LEGACY_V1 = "legacy_v1"
    EXACT_V2 = "exact_v2"
    CORRUPT = "corrupt"
    UNSUPPORTED_FUTURE = "unsupported_future"


class RetirementOutcome(StrEnum):
    """Result of one owner-controlled retirement attempt."""

    RECLAIMED = "reclaimed"
    DEFERRED_NOT_DUE = "deferred_not_due"
    DEFERRED_CONTENDED = "deferred_contended"
    DEFERRED_IO_ERROR = "deferred_io_error"
    REJECTED_IDENTITY = "rejected_identity"
    RECORD_REMOVED = "record_removed"
    LEGACY_EVIDENCE = "legacy_evidence"


def new_plugin_artifact_incarnation_id() -> str:
    """Return the canonical UUID4-hex identity shared by every artifact kind."""
    return uuid4().hex


def is_canonical_plugin_artifact_incarnation_id(value: object) -> TypeGuard[str]:
    """Return whether *value* is lowercase, 32-character UUID4 hex."""
    if not isinstance(value, str) or len(value) != 32:
        return False
    try:
        parsed = UUID(hex=value)
    except ValueError:
        return False
    return parsed.hex == value and parsed.version == 4


def is_canonical_plugin_artifact_digest(value: object) -> TypeGuard[str]:
    """Return whether *value* is one lowercase SHA-256 hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and value.lower() == value
        and all(character in "0123456789abcdef" for character in value)
    )


def normalize_inherited_fds(descriptors: Iterable[int]) -> tuple[int, ...]:
    """Validate and de-duplicate inherited descriptors without reordering."""
    normalized: list[int] = []
    seen: set[int] = set()
    for descriptor in descriptors:
        if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 0:
            raise ValueError("inherited descriptors must be non-negative integers")
        if descriptor not in seen:
            normalized.append(descriptor)
            seen.add(descriptor)
    return tuple(normalized)


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


def _normalized_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RetiringArtifactRecord:
    """Exact v2 deletion authority for one immutable artifact incarnation."""

    record_id: str
    artifact_kind: PluginArtifactKind
    semantic_key: str
    managed_path: Path
    manifest_path: Path
    incarnation_id: str
    manifest_schema_version: int
    artifact_digest: str
    retired_at: datetime
    not_before: datetime
    schema_version: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "managed_path", Path(self.managed_path))
        object.__setattr__(self, "manifest_path", Path(self.manifest_path))
        object.__setattr__(
            self,
            "retired_at",
            _normalized_utc(self.retired_at, field_name="retired_at"),
        )
        object.__setattr__(
            self,
            "not_before",
            _normalized_utc(self.not_before, field_name="not_before"),
        )
        if not self.record_id:
            raise ValueError("retiring artifact record_id must not be empty")
        if not self.semantic_key or not self.incarnation_id or not self.artifact_digest:
            raise ValueError("retiring artifact identity fields must not be empty")
        if not is_canonical_plugin_artifact_incarnation_id(self.incarnation_id):
            raise ValueError("retiring artifact incarnation_id must be canonical uuid4 hex")
        if not is_canonical_plugin_artifact_digest(self.artifact_digest):
            raise ValueError("retiring artifact digest must be lowercase SHA-256 hex")
        if not self.managed_path.is_absolute() or not self.manifest_path.is_absolute():
            raise ValueError("retiring artifact paths must be absolute")
        if self.manifest_schema_version < 1:
            raise ValueError("retiring artifact manifest schema version must be positive")
        if self.schema_version != 2:
            raise ValueError("retiring artifact record schema_version must be 2")
        if self.not_before < self.retired_at:
            raise ValueError("retiring artifact not_before cannot precede retired_at")

    @property
    def identity(self) -> PluginArtifactIdentity:
        return PluginArtifactIdentity(
            semantic_key=self.semantic_key,
            incarnation_id=self.incarnation_id,
            manifest_schema_version=self.manifest_schema_version,
            artifact_digest=self.artifact_digest,
            managed_path=self.managed_path,
            manifest_path=self.manifest_path,
        )


@dataclass(frozen=True, slots=True)
class LegacyRetiringEvidence:
    """Non-destructive evidence migrated from a path-only v1 record."""

    record_id: str
    version: str
    path: str
    retired_at: str
    recognized_kind: PluginArtifactKind | None
    rejection_reason: str | None

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("legacy retiring record_id must not be empty")
        if not self.path:
            raise ValueError("legacy retiring path must not be empty")
        if self.recognized_kind is None and not self.rejection_reason:
            raise ValueError("unclassified legacy evidence requires a rejection reason")
        if self.recognized_kind is not None and self.rejection_reason is not None:
            raise ValueError("recognized legacy evidence cannot carry a rejection reason")


@dataclass(frozen=True, slots=True)
class RetiringCacheReadResult:
    """Typed retirement-cache read that never collapses unsafe states to empty."""

    state: RetiringCacheState
    records: tuple[RetiringArtifactRecord, ...] = ()
    legacy_evidence: tuple[LegacyRetiringEvidence, ...] = ()
    schema_version: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RetiringAppendResult:
    """Stable record identity returned by idempotent queue append."""

    record_id: str
    created: bool


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
            if self.plugin_dir != self.identity.managed_path:
                raise ValueError(
                    "plugin launch path must match the leased artifact identity: "
                    f"{self.plugin_dir} != {self.identity.managed_path}"
                )
        if self.load_mode is not PluginLoadMode.IMPLICIT_INSTALLED and self.plugin_dir is None:
            raise ValueError(f"{self.load_mode.value} requires a plugin path")
        object.__setattr__(
            self,
            "inherited_fds",
            normalize_inherited_fds(self.inherited_fds),
        )

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
