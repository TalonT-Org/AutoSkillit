"""Plugin cache lifecycle: retiring cache, install locking, kitchen registry."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import psutil

from .io import read_versioned_json, write_versioned_json
from .logging import get_logger
from .paths import destination_location
from .types import (
    LegacyRetiringEvidence,
    PluginArtifactKind,
    RetiringAppendResult,
    RetiringArtifactRecord,
    RetiringCacheReadResult,
    RetiringCacheState,
)

logger = get_logger(__name__)

_ACTIVE_KITCHENS_SCHEMA_VERSION = 1
_RETIRING_CACHE_SCHEMA_VERSION = 2


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
    return RetiringArtifactRecord(
        record_id=str(raw["record_id"]),
        artifact_kind=PluginArtifactKind(str(raw["artifact_kind"])),
        semantic_key=str(raw["semantic_key"]),
        managed_path=Path(str(raw["managed_path"])),
        manifest_path=Path(str(raw["manifest_path"])),
        incarnation_id=str(raw["incarnation_id"]),
        manifest_schema_version=int(raw["manifest_schema_version"]),
        artifact_digest=str(raw["artifact_digest"]),
        retired_at=_parse_utc(raw["retired_at"], field_name="retired_at"),
        not_before=_parse_utc(raw["not_before"], field_name="not_before"),
        schema_version=int(raw["schema_version"]),
    )


def _legacy_from_json(raw: object) -> LegacyRetiringEvidence:
    if not isinstance(raw, dict):
        raise ValueError("legacy retiring evidence must be an object")
    kind_value = raw.get("recognized_kind")
    return LegacyRetiringEvidence(
        record_id=str(raw["record_id"]),
        version=str(raw["version"]),
        path=str(raw["path"]),
        retired_at=str(raw["retired_at"]),
        recognized_kind=PluginArtifactKind(str(kind_value)) if kind_value is not None else None,
        rejection_reason=(
            str(raw["rejection_reason"]) if raw.get("rejection_reason") is not None else None
        ),
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
        if not isinstance(schema_version, int):
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
        records_raw = raw.get("records", [])
        legacy_raw = raw.get("legacy_evidence", [])
        if not isinstance(records_raw, list) or not isinstance(legacy_raw, list):
            raise ValueError("v2 retirement arrays are malformed")
        return RetiringCacheReadResult(
            state=RetiringCacheState.EXACT_V2,
            records=tuple(_record_from_json(item) for item in records_raw),
            legacy_evidence=tuple(_legacy_from_json(item) for item in legacy_raw),
            schema_version=_RETIRING_CACHE_SCHEMA_VERSION,
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
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
        record.not_before,
    )


def append_retiring_record(record: RetiringArtifactRecord) -> RetiringAppendResult:
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
        _write_retiring_cache_unlocked((*records, record), evidence)
        return RetiringAppendResult(record_id=record.record_id, created=True)
    finally:
        fh.close()


def remove_retiring_records(record_ids: Iterable[str]) -> int:
    """Remove exact records or migrated evidence by stable record ID."""
    record_ids = frozenset(record_ids)
    if not record_ids:
        return 0
    fh = _open_lock(_retiring_cache_lock())
    try:
        state = _read_retiring_cache_unlocked()
        if state.state is RetiringCacheState.ABSENT:
            return 0
        if state.state is not RetiringCacheState.EXACT_V2:
            raise RuntimeError(f"retiring cache is not mutable in state {state.state.value}")
        records = tuple(record for record in state.records if record.record_id not in record_ids)
        evidence = tuple(
            item for item in state.legacy_evidence if item.record_id not in record_ids
        )
        removed = (len(state.records) - len(records)) + (
            len(state.legacy_evidence) - len(evidence)
        )
        if removed:
            _write_retiring_cache_unlocked(records, evidence)
        return removed
    finally:
        fh.close()


def due_retiring_records(now: datetime) -> tuple[RetiringArtifactRecord, ...]:
    """Return exact records whose persisted deadline is due."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("retirement sweep time must be timezone-aware")
    normalized_now = now.astimezone(UTC)
    state = read_retiring_cache()
    if state.state is not RetiringCacheState.EXACT_V2:
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


def kitchen_entry_alive(entry: dict) -> bool:
    """Return True if an active_kitchens.json entry's process is still running."""
    pid = entry.get("pid")
    if not isinstance(pid, int):
        return False
    create_time = entry.get("create_time")
    stored: float | None = float(create_time) if isinstance(create_time, (int, float)) else None
    return _pid_alive(pid, stored_create_time=stored)


def read_active_kitchens_registry() -> list[dict]:
    """Return the current active_kitchens.json entries (locked read).

    Public counterpart to the private ``_active_kitchens_path``/``_active_kitchens_lock``
    pair — callers outside this module must not reach into private submodule internals
    (REQ-ARCH-001), so this is the sanctioned read surface for registry consumers such
    as ``prune_stale_kitchen_state``.
    """
    akp = _active_kitchens_path()
    lock = _active_kitchens_lock()
    if not akp.exists():
        return []
    fh = _open_lock(lock)
    try:
        data = read_versioned_json(akp, _ACTIVE_KITCHENS_SCHEMA_VERSION, logger=logger)
        return data.get("kitchens", []) if data is not None else []
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


def register_active_kitchen(kitchen_id: str, pid: int, project_path: str) -> None:
    lock = _active_kitchens_lock()
    akp = _active_kitchens_path()
    fh = _open_lock(lock)
    try:
        entries: list[dict[str, object]] = []
        if akp.exists():
            data = read_versioned_json(akp, _ACTIVE_KITCHENS_SCHEMA_VERSION, logger=logger)
            entries = data.get("kitchens", []) if data is not None else []
        try:
            create_time: float | None = psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:
            create_time = None
        entries.append(
            {
                "kitchen_id": kitchen_id,
                "pid": pid,
                "create_time": create_time,
                "project_path": project_path,
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


def unregister_active_kitchen(kitchen_id: str) -> None:
    lock = _active_kitchens_lock()
    akp = _active_kitchens_path()
    fh = _open_lock(lock)
    try:
        entries: list[dict[str, object]] = []
        if akp.exists():
            data = read_versioned_json(akp, _ACTIVE_KITCHENS_SCHEMA_VERSION, logger=logger)
            entries = data.get("kitchens", []) if data is not None else []
        survivors = [e for e in entries if e.get("kitchen_id") != kitchen_id]
        write_versioned_json(
            akp,
            {"kitchens": survivors},
            schema_version=_ACTIVE_KITCHENS_SCHEMA_VERSION,
        )
    finally:
        fh.close()


def clear_kitchens_for_pid(pid: int) -> None:
    lock = _active_kitchens_lock()
    akp = _active_kitchens_path()
    fh = _open_lock(lock)
    try:
        entries: list[dict[str, object]] = []
        if akp.exists():
            data = read_versioned_json(akp, _ACTIVE_KITCHENS_SCHEMA_VERSION, logger=logger)
            entries = data.get("kitchens", []) if data is not None else []
        survivors = [e for e in entries if e.get("pid") != pid]
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
        data = read_versioned_json(akp, _ACTIVE_KITCHENS_SCHEMA_VERSION, logger=logger)
        if data is None:
            return False
        entries: list[dict[str, object]] = data.get("kitchens", [])
        survivors = []
        for entry in entries:
            pid = entry.get("pid")
            if not isinstance(pid, int):
                continue
            create_time = entry.get("create_time")
            stored: float | None = (
                float(create_time) if isinstance(create_time, (int, float)) else None
            )
            if _pid_alive(pid, stored_create_time=stored):
                survivors.append(entry)
        if len(survivors) < len(entries):
            try:
                write_versioned_json(
                    akp,
                    {"kitchens": survivors},
                    schema_version=_ACTIVE_KITCHENS_SCHEMA_VERSION,
                )
            except OSError as exc:
                logger.warning("any_kitchen_open: failed to persist pruned kitchens: %s", exc)
        if project_path is not None:
            return any(entry.get("project_path") == project_path for entry in survivors)
        return len(survivors) > 0
    finally:
        fh.close()
