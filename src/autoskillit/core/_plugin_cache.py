"""Plugin cache lifecycle: retiring cache, install locking, kitchen registry."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import psutil

from .io import (
    _AtomicWriteDurabilityError,
    write_versioned_json,
)
from .logging import get_logger, log_plugin_artifact_lifecycle
from .paths import destination_location
from .runtime.artifact_lease import ArtifactLease, ArtifactLeaseContention
from .types import (
    LegacyRetiringEvidence,
    PluginArtifactIdentity,
    PluginArtifactKind,
    PluginArtifactUnavailableError,
    PluginArtifactValidationError,
    RetirementOutcome,
    RetiringAppendResult,
    RetiringArtifactRecord,
    RetiringCacheReadResult,
    RetiringCacheState,
)

logger = get_logger(__name__)

_ACTIVE_KITCHENS_SCHEMA_VERSION = 2
_ACTIVE_KITCHEN_FIELDS = frozenset(
    {"kitchen_id", "pid", "create_time", "project_path", "opened_at"}
)
_RETIRING_CACHE_SCHEMA_VERSION = 2
_RETIRING_CACHE_V2_FIELDS = frozenset(
    {
        "schema_version",
        "records",
        "legacy_evidence",
    }
)
_RETIRING_RECORD_FIELDS = frozenset(
    {
        "record_id",
        "artifact_kind",
        "semantic_key",
        "managed_path",
        "manifest_path",
        "incarnation_id",
        "manifest_schema_version",
        "artifact_digest",
        "retired_at",
        "not_before",
        "schema_version",
    }
)


def _autoskillit_home() -> Path:
    return Path.home() / ".autoskillit"


def _retiring_cache_path() -> Path:
    return _autoskillit_home() / "retiring_cache.json"


def _retiring_cache_lock() -> Path:
    return _autoskillit_home() / "retiring_cache.lock"


def _active_kitchens_path() -> Path:
    return _autoskillit_home() / "active_kitchens.json"


def _active_kitchens_lock() -> Path:
    return _autoskillit_home() / "active_kitchens.lock"


def _install_lock_path() -> Path:
    return _autoskillit_home() / "install.lock"


def _open_lock(lock_path: Path) -> IO[str]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
    except Exception:
        fh.close()
        raise
    return fh


def _parse_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an RFC3339 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _record_from_json(raw: object) -> RetiringArtifactRecord:
    if not isinstance(raw, dict):
        raise ValueError("retiring record must be an object")
    if frozenset(raw) != _RETIRING_RECORD_FIELDS:
        raise ValueError("retiring record fields do not match the exact v2 schema")
    string_fields = (
        "record_id",
        "artifact_kind",
        "semantic_key",
        "managed_path",
        "manifest_path",
        "incarnation_id",
        "artifact_digest",
    )
    for field in string_fields:
        if not isinstance(raw.get(field), str):
            raise ValueError(f"retiring record {field} must be a string")
    integer_fields = ("manifest_schema_version", "schema_version")
    for field in integer_fields:
        if type(raw.get(field)) is not int:
            raise ValueError(f"retiring record {field} must be an integer")
    return RetiringArtifactRecord(
        record_id=raw["record_id"],
        artifact_kind=PluginArtifactKind(raw["artifact_kind"]),
        semantic_key=raw["semantic_key"],
        managed_path=Path(raw["managed_path"]),
        manifest_path=Path(raw["manifest_path"]),
        incarnation_id=raw["incarnation_id"],
        manifest_schema_version=raw["manifest_schema_version"],
        artifact_digest=raw["artifact_digest"],
        retired_at=_parse_utc(raw["retired_at"], field_name="retired_at"),
        not_before=_parse_utc(raw["not_before"], field_name="not_before"),
        schema_version=raw["schema_version"],
    )


def _legacy_from_json(raw: object) -> LegacyRetiringEvidence:
    if not isinstance(raw, dict):
        raise ValueError("legacy retiring evidence must be an object")
    for field in ("record_id", "version", "path", "retired_at"):
        if not isinstance(raw.get(field), str):
            raise ValueError(f"legacy retiring evidence {field} must be a string")
    kind_value = raw.get("recognized_kind")
    if kind_value is not None and not isinstance(kind_value, str):
        raise ValueError("legacy retiring evidence recognized_kind must be a string or null")
    rejection_reason = raw.get("rejection_reason")
    if rejection_reason is not None and not isinstance(rejection_reason, str):
        raise ValueError("legacy retiring evidence rejection_reason must be a string or null")
    return LegacyRetiringEvidence(
        record_id=raw["record_id"],
        version=raw["version"],
        path=raw["path"],
        retired_at=raw["retired_at"],
        recognized_kind=PluginArtifactKind(kind_value) if kind_value is not None else None,
        rejection_reason=rejection_reason,
    )


def _read_retiring_cache_unlocked() -> RetiringCacheReadResult:
    cache = _retiring_cache_path()
    if not cache.exists():
        return RetiringCacheReadResult(state=RetiringCacheState.ABSENT)
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("retiring cache root must be an object")
        schema_version = raw.get("schema_version")
        if type(schema_version) is not int:
            raise ValueError("retiring cache schema_version must be an integer")
        if schema_version > _RETIRING_CACHE_SCHEMA_VERSION:
            return RetiringCacheReadResult(
                state=RetiringCacheState.UNSUPPORTED_FUTURE,
                schema_version=schema_version,
            )
        if schema_version == 1:
            entries = raw.get("retiring")
            if not isinstance(entries, list):
                raise ValueError("v1 retiring cache requires a retiring array")
            for entry in entries:
                if not isinstance(entry, dict) or not all(
                    isinstance(entry.get(field), str)
                    for field in ("version", "path", "retired_at")
                ):
                    raise ValueError("v1 retiring cache entry is malformed")
            return RetiringCacheReadResult(
                state=RetiringCacheState.LEGACY_V1,
                schema_version=1,
            )
        if schema_version != _RETIRING_CACHE_SCHEMA_VERSION:
            raise ValueError(f"unsupported retiring cache schema {schema_version}")
        if frozenset(raw) != _RETIRING_CACHE_V2_FIELDS:
            raise ValueError("v2 retiring cache root has unexpected fields")
        records_raw = raw["records"]
        legacy_raw = raw["legacy_evidence"]
        if not isinstance(records_raw, list) or not isinstance(legacy_raw, list):
            raise ValueError("v2 retirement arrays are malformed")
        records = tuple(_record_from_json(item) for item in records_raw)
        legacy_evidence = tuple(_legacy_from_json(item) for item in legacy_raw)
        record_ids = tuple(record.record_id for record in records) + tuple(
            item.record_id for item in legacy_evidence
        )
        if len(frozenset(record_ids)) != len(record_ids):
            raise ValueError("v2 retirement record IDs must be unique")
        return RetiringCacheReadResult(
            state=RetiringCacheState.EXACT_V2,
            records=records,
            legacy_evidence=legacy_evidence,
            schema_version=_RETIRING_CACHE_SCHEMA_VERSION,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        json.JSONDecodeError,
    ) as exc:
        return RetiringCacheReadResult(
            state=RetiringCacheState.CORRUPT,
            error=str(exc),
        )


def read_retiring_cache() -> RetiringCacheReadResult:
    """Read and classify the complete retirement cache under its lock."""
    fh = _open_lock(_retiring_cache_lock())
    try:
        return _read_retiring_cache_unlocked()
    finally:
        fh.close()


def _record_to_json(record: RetiringArtifactRecord) -> dict[str, object]:
    return {
        "record_id": record.record_id,
        "artifact_kind": record.artifact_kind.value,
        "semantic_key": record.semantic_key,
        "managed_path": str(record.managed_path),
        "manifest_path": str(record.manifest_path),
        "incarnation_id": record.incarnation_id,
        "manifest_schema_version": record.manifest_schema_version,
        "artifact_digest": record.artifact_digest,
        "retired_at": record.retired_at.isoformat(),
        "not_before": record.not_before.isoformat(),
        "schema_version": record.schema_version,
    }


def _legacy_to_json(evidence: LegacyRetiringEvidence) -> dict[str, object]:
    return {
        "record_id": evidence.record_id,
        "version": evidence.version,
        "path": evidence.path,
        "retired_at": evidence.retired_at,
        "recognized_kind": (
            evidence.recognized_kind.value if evidence.recognized_kind is not None else None
        ),
        "rejection_reason": evidence.rejection_reason,
    }


def _write_retiring_cache_unlocked(
    records: tuple[RetiringArtifactRecord, ...],
    legacy_evidence: tuple[LegacyRetiringEvidence, ...],
) -> None:
    write_versioned_json(
        _retiring_cache_path(),
        {
            "records": [_record_to_json(record) for record in records],
            "legacy_evidence": [_legacy_to_json(item) for item in legacy_evidence],
        },
        schema_version=_RETIRING_CACHE_SCHEMA_VERSION,
        strict_durability=True,
    )


def _legacy_record_id(version: str, path: str, retired_at: str) -> str:
    payload = "\0".join((version, path, retired_at)).encode()
    return hashlib.sha256(payload).hexdigest()


def _classify_legacy_path(
    path: str,
    managed_roots: Mapping[PluginArtifactKind, Path],
) -> tuple[PluginArtifactKind | None, str | None]:
    supplied = Path(path)
    if not supplied.is_absolute():
        return None, "legacy path is not absolute"
    if supplied.is_symlink():
        return None, "legacy path is a symlink"
    try:
        location = destination_location(supplied)
    except (OSError, ValueError) as exc:
        return None, f"legacy path cannot be located: {exc}"
    for kind, root in managed_roots.items():
        try:
            managed_root = Path(root).expanduser().resolve(strict=False)
        except OSError:
            continue
        if location != managed_root and location.is_relative_to(managed_root):
            return kind, None
    return None, "legacy path is outside known managed roots"


def migrate_retiring_cache_v1(
    managed_roots: Mapping[PluginArtifactKind, Path],
) -> RetiringCacheReadResult:
    """Persist v1 path-only records as non-destructive typed evidence."""
    fh = _open_lock(_retiring_cache_lock())
    try:
        state = _read_retiring_cache_unlocked()
        if state.state is not RetiringCacheState.LEGACY_V1:
            return state
        raw = json.loads(_retiring_cache_path().read_text(encoding="utf-8"))
        seen: set[tuple[str, str, str]] = set()
        evidence: list[LegacyRetiringEvidence] = []
        for entry in raw["retiring"]:
            key = (entry["version"], entry["path"], entry["retired_at"])
            if key in seen:
                continue
            seen.add(key)
            kind, reason = _classify_legacy_path(entry["path"], managed_roots)
            evidence.append(
                LegacyRetiringEvidence(
                    record_id=_legacy_record_id(*key),
                    version=entry["version"],
                    path=entry["path"],
                    retired_at=entry["retired_at"],
                    recognized_kind=kind,
                    rejection_reason=reason,
                )
            )
        result = RetiringCacheReadResult(
            state=RetiringCacheState.EXACT_V2,
            legacy_evidence=tuple(evidence),
            schema_version=_RETIRING_CACHE_SCHEMA_VERSION,
        )
        _write_retiring_cache_unlocked(result.records, result.legacy_evidence)
        return result
    finally:
        fh.close()


def _retirement_intent(record: RetiringArtifactRecord) -> tuple[object, ...]:
    return (
        record.artifact_kind,
        record.semantic_key,
        record.managed_path,
        record.manifest_path,
        record.incarnation_id,
        record.manifest_schema_version,
        record.artifact_digest,
    )


def _retirement_staging_path(record: RetiringArtifactRecord) -> Path:
    record_digest = hashlib.sha256(record.record_id.encode()).hexdigest()[:16]
    return record.managed_path.parent / (
        f".{record.managed_path.name}.autoskillit-retiring-{record_digest}"
    )


def append_retiring_record(
    record: RetiringArtifactRecord,
    *,
    on_persisted: Callable[[str], None] | None = None,
) -> RetiringAppendResult:
    """Append one exact v2 record, preserving first-seen order and intent identity."""
    fh = _open_lock(_retiring_cache_lock())
    try:
        state = _read_retiring_cache_unlocked()
        if state.state is RetiringCacheState.ABSENT:
            records: tuple[RetiringArtifactRecord, ...] = ()
            evidence: tuple[LegacyRetiringEvidence, ...] = ()
        elif state.state is RetiringCacheState.EXACT_V2:
            records = state.records
            evidence = state.legacy_evidence
        else:
            raise RuntimeError(f"retiring cache is not mutable in state {state.state.value}")
        intent = _retirement_intent(record)
        for existing in records:
            if existing.record_id == record.record_id and existing != record:
                raise ValueError(
                    f"retiring record_id is already bound to another record: {record.record_id}"
                )
            if _retirement_intent(existing) == intent:
                return RetiringAppendResult(record_id=existing.record_id, created=False)
        try:
            _write_retiring_cache_unlocked((*records, record), evidence)
        except _AtomicWriteDurabilityError:
            if on_persisted is not None:
                on_persisted(record.record_id)
            raise
        if on_persisted is not None:
            on_persisted(record.record_id)
        return RetiringAppendResult(record_id=record.record_id, created=True)
    finally:
        fh.close()


def remove_retiring_records(record_ids: Iterable[str]) -> int:
    """Remove exact records or migrated evidence by stable record ID."""
    ids = frozenset(record_ids)
    if not ids:
        return 0
    fh = _open_lock(_retiring_cache_lock())
    try:
        state = _read_retiring_cache_unlocked()
        if state.state is RetiringCacheState.ABSENT:
            return 0
        if state.state is not RetiringCacheState.EXACT_V2:
            raise RuntimeError(f"retiring cache is not mutable in state {state.state.value}")
        records = tuple(record for record in state.records if record.record_id not in ids)
        evidence = tuple(item for item in state.legacy_evidence if item.record_id not in ids)
        removed = len(state.records) - len(records) + len(state.legacy_evidence) - len(evidence)
        if removed:
            _write_retiring_cache_unlocked(records, evidence)
        return removed
    finally:
        fh.close()


def _read_exact_retiring_cache(*, operation: str) -> RetiringCacheReadResult | None:
    state = read_retiring_cache()
    if state.state is RetiringCacheState.ABSENT:
        return None
    if state.state is not RetiringCacheState.EXACT_V2:
        raise RuntimeError(f"retiring cache cannot {operation} in state {state.state.value}")
    return state


def due_retiring_records(now: datetime) -> tuple[RetiringArtifactRecord, ...]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("retirement sweep time must be timezone-aware")
    normalized_now = now.astimezone(UTC)
    state = _read_exact_retiring_cache(operation="enumerate due records")
    if state is None:
        return ()
    return tuple(record for record in state.records if record.not_before <= normalized_now)


class _InstallLock:
    """Exclusive fcntl lock for the autoskillit install critical section."""

    def __init__(self) -> None:
        self._lock_file: IO[str] | None = None

    def __enter__(self) -> _InstallLock:
        self._lock_file = _open_lock(_install_lock_path())
        return self

    def __exit__(self, *_: object) -> None:
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None


class PluginArtifactRetirementEngine:
    """Shared exact-identity retirement algorithm parameterized by artifact hooks."""

    def __init__(
        self,
        *,
        managed_root: Path,
        artifact_kind: PluginArtifactKind,
        manifest_path: Callable[[Path], Path],
        lease_path: Callable[[Path], Path],
        current_identity: Callable[[RetiringArtifactRecord], PluginArtifactIdentity],
        logger: Any,
        is_current: Callable[[Path], bool] | None = None,
    ) -> None:
        self.managed_root = Path(managed_root).expanduser().resolve(strict=False)
        self.artifact_kind = artifact_kind
        self._manifest_path = manifest_path
        self._lease_path = lease_path
        self._current_identity = current_identity
        self._logger = logger
        self._is_current = is_current

    def contains(self, path: Path) -> bool:
        """Return whether *path* is a child artifact owned by this engine."""
        try:
            location = destination_location(Path(path))
        except (OSError, ValueError):
            return False
        return location != self.managed_root and location.is_relative_to(self.managed_root)

    def enqueue_retirement(
        self,
        identity: PluginArtifactIdentity,
        not_before: datetime,
        *,
        on_persisted: Callable[[str], None] | None = None,
    ) -> RetiringAppendResult:
        """Queue one exact incarnation after validating owner-specific paths."""
        if not self.contains(identity.managed_path):
            raise PluginArtifactValidationError(
                f"{self.artifact_kind.value} artifact is outside managed root: "
                f"{identity.managed_path}"
            )
        if identity.manifest_path != self._manifest_path(identity.managed_path):
            raise PluginArtifactValidationError(
                f"{self.artifact_kind.value} artifact manifest path is not canonical: "
                f"{identity.manifest_path}"
            )
        retired_at = datetime.now(UTC)
        if not_before.tzinfo is not None and not_before.utcoffset() is not None:
            retired_at = min(retired_at, not_before.astimezone(UTC))
        result = append_retiring_record(
            RetiringArtifactRecord(
                record_id=uuid.uuid4().hex,
                artifact_kind=self.artifact_kind,
                semantic_key=identity.semantic_key,
                managed_path=identity.managed_path,
                manifest_path=identity.manifest_path,
                incarnation_id=identity.incarnation_id,
                manifest_schema_version=identity.manifest_schema_version,
                artifact_digest=identity.artifact_digest,
                retired_at=retired_at,
                not_before=not_before,
            ),
            on_persisted=on_persisted,
        )
        log_plugin_artifact_lifecycle(
            self._logger,
            action="retire",
            outcome="succeeded",
            artifact_kind=self.artifact_kind.value,
            semantic_key=identity.semantic_key,
            incarnation=identity.incarnation_id,
            not_before=not_before,
        )
        return result

    def cancel_obsolete_retirements(self, identity: PluginArtifactIdentity) -> tuple[str, ...]:
        state = _read_exact_retiring_cache(operation="cancel obsolete records")
        if state is None:
            return ()
        record_ids = tuple(
            record.record_id
            for record in state.records
            if record.artifact_kind is self.artifact_kind
            and record.managed_path == identity.managed_path
        ) + tuple(
            evidence.record_id
            for evidence in state.legacy_evidence
            if evidence.recognized_kind is self.artifact_kind
            and Path(evidence.path) == identity.managed_path
        )
        if not record_ids:
            return ()
        remove_retiring_records(record_ids)
        log_plugin_artifact_lifecycle(
            self._logger,
            action="cancel_retirement",
            outcome="succeeded",
            artifact_kind=self.artifact_kind.value,
            semantic_key=identity.semantic_key,
            incarnation=identity.incarnation_id,
        )
        return record_ids

    def try_reclaim(self, record: RetiringArtifactRecord, now: datetime) -> RetirementOutcome:
        """Reclaim one queued record only while its lease and identity remain exact."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("artifact retirement sweep time must be timezone-aware")
        now = now.astimezone(UTC)
        if record.artifact_kind is not self.artifact_kind:
            return self._log_reclaim(record, RetirementOutcome.REJECTED_IDENTITY)
        if now < record.not_before:
            return RetirementOutcome.DEFERRED_NOT_DUE
        if not self.contains(record.managed_path):
            return self._log_reclaim(record, RetirementOutcome.REJECTED_IDENTITY)
        try:
            writer = ArtifactLease.acquire_exclusive(
                self._lease_path(record.managed_path),
                blocking=False,
            )
        except ArtifactLeaseContention as exc:
            return self._log_reclaim(
                record,
                RetirementOutcome.DEFERRED_CONTENDED,
                detail=str(exc),
            )
        except (OSError, RuntimeError) as exc:
            return self._log_reclaim(
                record,
                RetirementOutcome.DEFERRED_IO_ERROR,
                detail=str(exc),
            )
        try:
            with _InstallLock():
                state = read_retiring_cache()
                if state.state is RetiringCacheState.ABSENT:
                    return RetirementOutcome.RECORD_REMOVED
                if state.state is not RetiringCacheState.EXACT_V2:
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.DEFERRED_IO_ERROR,
                        detail=f"retiring cache became unsafe: {state.state.value}",
                    )
                queued = next(
                    (
                        current
                        for current in state.records
                        if current.record_id == record.record_id
                    ),
                    None,
                )
                if queued is None:
                    return RetirementOutcome.RECORD_REMOVED
                if queued != record:
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.REJECTED_IDENTITY,
                    )
                if now < queued.not_before:
                    return RetirementOutcome.DEFERRED_NOT_DUE
                if self._is_current is not None and self._is_current(record.managed_path):
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.DEFERRED_CONTENDED,
                        detail="managed_path is the actively selected generation",
                    )
                staging_path = _retirement_staging_path(record)
                managed_exists = record.managed_path.exists() or record.managed_path.is_symlink()
                manifest_exists = (
                    record.manifest_path.exists() or record.manifest_path.is_symlink()
                )
                staging_exists = staging_path.exists() or staging_path.is_symlink()
                if not managed_exists and not manifest_exists and not staging_exists:
                    remove_retiring_records((record.record_id,))
                    return RetirementOutcome.RECORD_REMOVED
                if staging_exists:
                    if managed_exists or staging_path.is_symlink() or not staging_path.is_dir():
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.DEFERRED_IO_ERROR,
                            detail=f"retirement staging path is ambiguous: {staging_path}",
                        )
                else:
                    try:
                        current = self._current_identity(record)
                    except PluginArtifactUnavailableError as exc:
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.DEFERRED_IO_ERROR,
                            detail=str(exc),
                        )
                    except PluginArtifactValidationError:
                        remove_retiring_records((record.record_id,))
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.REJECTED_IDENTITY,
                            failed_validation=True,
                        )
                    if current != record.identity:
                        remove_retiring_records((record.record_id,))
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.REJECTED_IDENTITY,
                        )
                    try:
                        os.rename(record.managed_path, staging_path)
                    except OSError as exc:
                        return self._log_reclaim(
                            record,
                            RetirementOutcome.DEFERRED_IO_ERROR,
                            detail=str(exc),
                        )
                try:
                    if record.manifest_path.is_file() or record.manifest_path.is_symlink():
                        record.manifest_path.unlink()
                    elif record.manifest_path.exists():
                        raise OSError(
                            f"retirement manifest is not removable: {record.manifest_path}"
                        )
                    shutil.rmtree(staging_path)
                    remove_retiring_records((record.record_id,))
                except OSError as exc:
                    return self._log_reclaim(
                        record,
                        RetirementOutcome.DEFERRED_IO_ERROR,
                        detail=str(exc),
                    )
                return self._log_reclaim(record, RetirementOutcome.RECLAIMED)
        finally:
            writer.close_preserving()

    def _log_reclaim(
        self,
        record: RetiringArtifactRecord,
        outcome: RetirementOutcome,
        *,
        detail: str | None = None,
        failed_validation: bool = False,
    ) -> RetirementOutcome:
        event_outcome = {
            RetirementOutcome.RECLAIMED: "succeeded",
            RetirementOutcome.DEFERRED_CONTENDED: "deferred_contended",
            RetirementOutcome.DEFERRED_IO_ERROR: "deferred_io_error",
            RetirementOutcome.REJECTED_IDENTITY: "rejected_identity",
        }[outcome]
        if failed_validation:
            event_outcome = "failed_validation"
        log_plugin_artifact_lifecycle(
            self._logger,
            action="reclaim",
            outcome=event_outcome,
            artifact_kind=self.artifact_kind.value,
            semantic_key=record.semantic_key,
            incarnation=record.incarnation_id,
            not_before=record.not_before,
            contention_detail=detail,
        )
        return outcome


@dataclass(frozen=True, slots=True)
class KitchenProcessIdentity:
    kitchen_id: str
    pid: int
    create_time: float
    project_path: str


def sample_kitchen_process_identity(
    kitchen_id: str,
    pid: int,
    project_path: str | os.PathLike[str],
) -> KitchenProcessIdentity:
    """Resolve one complete process incarnation for the kitchen lifetime."""
    if not isinstance(kitchen_id, str) or not kitchen_id:
        raise ValueError("kitchen_id must be a nonempty string")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    resolved_project = str(Path(project_path).resolve(strict=True))
    create_time = float(psutil.Process(pid).create_time())
    return KitchenProcessIdentity(kitchen_id, pid, create_time, resolved_project)


def _identity_from_entry(entry: object) -> KitchenProcessIdentity:
    if not isinstance(entry, dict) or frozenset(entry) != _ACTIVE_KITCHEN_FIELDS:
        raise ValueError("active kitchen entry does not match the exact v2 schema")
    kitchen_id = entry.get("kitchen_id")
    pid = entry.get("pid")
    create_time = entry.get("create_time")
    project_path = entry.get("project_path")
    opened_at = entry.get("opened_at")
    if not isinstance(kitchen_id, str) or not kitchen_id:
        raise ValueError("active kitchen kitchen_id must be nonempty")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("active kitchen pid must be a positive integer")
    if (
        isinstance(create_time, bool)
        or not isinstance(create_time, (int, float))
        or create_time <= 0
    ):
        raise ValueError("active kitchen create_time must be a positive number")
    if not isinstance(project_path, str) or not project_path:
        raise ValueError("active kitchen project_path must be nonempty")
    _parse_utc(opened_at, field_name="active kitchen opened_at")
    return KitchenProcessIdentity(kitchen_id, pid, float(create_time), project_path)


def _read_active_kitchens_unlocked(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or frozenset(raw) != {"schema_version", "kitchens"}:
        raise ValueError("active kitchen registry does not match the exact v2 schema")
    if raw.get("schema_version") != _ACTIVE_KITCHENS_SCHEMA_VERSION:
        raise ValueError("active kitchen registry schema is unsupported")
    kitchens = raw.get("kitchens")
    if not isinstance(kitchens, list):
        raise ValueError("active kitchen registry kitchens must be a list")
    entries: list[dict[str, object]] = []
    for entry in kitchens:
        _identity_from_entry(entry)
        entries.append(dict(entry))
    return entries


def kitchen_entry_alive(entry: dict) -> bool:
    """Return True if an active_kitchens.json entry's process is still running."""
    try:
        identity = _identity_from_entry(entry)
    except ValueError:
        return False
    return _pid_alive(identity.pid, stored_create_time=identity.create_time)


def read_active_kitchens_registry() -> list[dict]:
    """Return the current active_kitchens.json entries (locked read).

    Public counterpart to the private ``_active_kitchens_path``/``_active_kitchens_lock``
    pair — callers outside this module must not reach into private submodule internals
    (REQ-ARCH-001), so this is the sanctioned read surface for registry consumers such
    as ``prune_stale_kitchen_state``.
    """
    akp = _active_kitchens_path()
    lock = _active_kitchens_lock()
    fh = _open_lock(lock)
    try:
        return _read_active_kitchens_unlocked(akp)
    finally:
        fh.close()


def _pid_alive(pid: int, stored_create_time: float | None = None) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        if stored_create_time is not None:
            try:
                actual = psutil.Process(pid).create_time()
                return abs(actual - stored_create_time) < 1.0
            except psutil.NoSuchProcess:
                return False
        return True
    if stored_create_time is not None:
        try:
            actual = psutil.Process(pid).create_time()
            return abs(actual - stored_create_time) < 1.0
        except psutil.NoSuchProcess:
            return False
    return True


def register_active_kitchen(identity: KitchenProcessIdentity) -> None:
    lock = _active_kitchens_lock()
    akp = _active_kitchens_path()
    fh = _open_lock(lock)
    try:
        entries = [
            entry
            for entry in _read_active_kitchens_unlocked(akp)
            if _identity_from_entry(entry) != identity
        ]
        entries.append(
            {
                "kitchen_id": identity.kitchen_id,
                "pid": identity.pid,
                "create_time": identity.create_time,
                "project_path": identity.project_path,
                "opened_at": datetime.now(UTC).isoformat(),
            }
        )
        write_versioned_json(
            akp,
            {"kitchens": entries},
            schema_version=_ACTIVE_KITCHENS_SCHEMA_VERSION,
        )
    finally:
        fh.close()


def unregister_active_kitchen(identity: KitchenProcessIdentity) -> None:
    lock = _active_kitchens_lock()
    akp = _active_kitchens_path()
    fh = _open_lock(lock)
    try:
        entries = _read_active_kitchens_unlocked(akp)
        survivors = [e for e in entries if _identity_from_entry(e) != identity]
        write_versioned_json(
            akp,
            {"kitchens": survivors},
            schema_version=_ACTIVE_KITCHENS_SCHEMA_VERSION,
        )
    finally:
        fh.close()


def any_kitchen_open(project_path: str | None = None) -> bool:
    akp = _active_kitchens_path()
    lock = _active_kitchens_lock()
    if not akp.exists():
        return False
    fh = _open_lock(lock)
    try:
        try:
            entries = _read_active_kitchens_unlocked(akp)
        except (OSError, ValueError, json.JSONDecodeError):
            return True
        survivors = [entry for entry in entries if kitchen_entry_alive(entry)]
        if project_path is not None:
            return any(entry.get("project_path") == project_path for entry in survivors)
        return len(survivors) > 0
    finally:
        fh.close()
