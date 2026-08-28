"""Retiring-cache persistence, schema, lock acquisition, and mutation primitives.

The install lock and the mutation primitives (``read_retiring_cache``,
``append_retiring_record``, ``remove_retiring_records``,
``_write_retiring_cache_unlocked``) live together so the lifecycle lock is
never bypassed by an off-module caller (issue #4689 invariant, preserved by
the source ratchet in ``tests/infra/test_plugin_source_ratchets.py``).
``_parse_utc`` lives here too — ``_active_kitchens._identity_from_entry`` is
its only off-module caller.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from .io import _AtomicWriteDurabilityError, atomic_write, write_versioned_json
from .logging import get_logger
from .paths import destination_location
from .runtime.artifact_lease import ARTIFACT_LEASE_TIMEOUT_SECONDS, acquire_flock_with_timeout
from .types import (
    LegacyRetiringEvidence,
    ManagedHome,
    PluginArtifactKind,
    QuarantinedRetiringRecord,
    RetiringAppendResult,
    RetiringArtifactRecord,
    RetiringCacheReadResult,
    RetiringCacheRepairResult,
    RetiringCacheState,
    managed_home,
)

logger = get_logger(__name__)

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
_LEGACY_RETIRING_EVIDENCE_FIELDS = frozenset(
    {
        "record_id",
        "version",
        "path",
        "retired_at",
        "recognized_kind",
        "rejection_reason",
    }
)


def _autoskillit_home(home: ManagedHome) -> Path:
    return home.autoskillit_dir


def _retiring_cache_path(home: ManagedHome) -> Path:
    return _autoskillit_home(home) / "retiring_cache.json"


def _retiring_cache_lock(home: ManagedHome) -> Path:
    return _autoskillit_home(home) / "retiring_cache.lock"


def _install_lock_path(home: ManagedHome) -> Path:
    return _autoskillit_home(home) / "install.lock"


def _open_lock(lock_path: Path) -> IO[str]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        acquire_flock_with_timeout(
            fh.fileno(),
            operation=fcntl.LOCK_EX,
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
            path=lock_path,
        )
    except BaseException:
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
    if frozenset(raw) != _LEGACY_RETIRING_EVIDENCE_FIELDS:
        raise ValueError("legacy retiring evidence fields do not match the exact v2 schema")
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


def _read_retiring_cache_unlocked(home: ManagedHome) -> RetiringCacheReadResult:
    cache = _retiring_cache_path(home)
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
        records: list[RetiringArtifactRecord] = []
        quarantined_records: list[QuarantinedRetiringRecord] = []
        quarantined_record_ids: list[str] = []
        for item in records_raw:
            try:
                records.append(_record_from_json(item))
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                quarantined_records.append(
                    QuarantinedRetiringRecord(
                        raw_json=json.dumps(item, sort_keys=True),
                        reason=str(exc),
                    )
                )
                if isinstance(item, dict) and isinstance(item.get("record_id"), str):
                    quarantined_record_ids.append(item["record_id"])
        legacy_evidence: list[LegacyRetiringEvidence] = []
        quarantined_legacy_evidence: list[QuarantinedRetiringRecord] = []
        for item in legacy_raw:
            try:
                legacy_evidence.append(_legacy_from_json(item))
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                quarantined_legacy_evidence.append(
                    QuarantinedRetiringRecord(
                        raw_json=json.dumps(item, sort_keys=True),
                        reason=str(exc),
                    )
                )
                if isinstance(item, dict) and isinstance(item.get("record_id"), str):
                    quarantined_record_ids.append(item["record_id"])
        record_ids = (
            tuple(record.record_id for record in records)
            + tuple(item.record_id for item in legacy_evidence)
            + tuple(quarantined_record_ids)
        )
        if len(frozenset(record_ids)) != len(record_ids):
            raise ValueError("v2 retirement record IDs must be unique")
        return RetiringCacheReadResult(
            state=RetiringCacheState.EXACT_V2,
            records=tuple(records),
            legacy_evidence=tuple(legacy_evidence),
            quarantined_records=tuple(quarantined_records),
            quarantined_legacy_evidence=tuple(quarantined_legacy_evidence),
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


def read_retiring_cache(*, home: ManagedHome | None = None) -> RetiringCacheReadResult:
    """Read and classify the complete retirement cache under its lock."""
    resolved_home = home if home is not None else managed_home()
    fh = _open_lock(_retiring_cache_lock(resolved_home))
    try:
        return _read_retiring_cache_unlocked(resolved_home)
    finally:
        fh.close()


def _next_corrupt_sidecar_path(cache: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    base = f"{cache.stem}.corrupt-{timestamp}"
    candidate = cache.with_name(f"{base}{cache.suffix}")
    suffix = 1
    while candidate.exists():
        candidate = cache.with_name(f"{base}-{suffix}{cache.suffix}")
        suffix += 1
    return candidate


def _salvage_retiring_records(
    raw_bytes: bytes,
) -> tuple[tuple[RetiringArtifactRecord, ...], tuple[QuarantinedRetiringRecord, ...]]:
    """Recover independently valid records from a corrupt JSON root prefix."""
    try:
        text = raw_bytes.decode("utf-8")
        recovered, _ = json.JSONDecoder().raw_decode(text.lstrip())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (), ()
    if not isinstance(recovered, dict) or not isinstance(recovered.get("records"), list):
        return (), ()

    raw_records = recovered["records"]
    record_id_counts: dict[str, int] = {}
    for raw_record in raw_records:
        if isinstance(raw_record, dict) and isinstance(raw_record.get("record_id"), str):
            record_id = raw_record["record_id"]
            record_id_counts[record_id] = record_id_counts.get(record_id, 0) + 1

    salvaged: list[RetiringArtifactRecord] = []
    quarantined: list[QuarantinedRetiringRecord] = []
    for raw_record in raw_records:
        if (
            isinstance(raw_record, dict)
            and isinstance(raw_record.get("record_id"), str)
            and record_id_counts[raw_record["record_id"]] > 1
        ):
            continue
        raw_json = json.dumps(raw_record, separators=(",", ":"), sort_keys=True)
        try:
            record = _record_from_json(raw_record)
        except (TypeError, ValueError) as exc:
            quarantined.append(QuarantinedRetiringRecord(raw_json=raw_json, reason=str(exc)))
            continue
        salvaged.append(record)
    return tuple(salvaged), tuple(quarantined)


def repair_corrupt_retiring_cache(*, home: ManagedHome | None = None) -> RetiringCacheRepairResult:
    """Rebuild a corrupt cache after durably preserving its original bytes.

    Unsupported future schemas are never rewritten because this version cannot
    determine which records carry authority.
    """
    resolved_home = home if home is not None else managed_home()
    fh = _open_lock(_retiring_cache_lock(resolved_home))
    try:
        state = _read_retiring_cache_unlocked(resolved_home)
        if state.state is not RetiringCacheState.CORRUPT:
            return RetiringCacheRepairResult(repaired=False, state=state.state)

        cache_path = _retiring_cache_path(resolved_home)
        original = cache_path.read_bytes()
        while True:
            sidecar = _next_corrupt_sidecar_path(cache_path)
            try:
                atomic_write(
                    sidecar,
                    original,
                    strict_durability=True,
                    exclusive=True,
                )
            except FileExistsError:
                continue
            break

        salvaged, quarantined = _salvage_retiring_records(original)
        _write_retiring_cache_unlocked(
            resolved_home,
            salvaged,
            (),
            quarantined_records=quarantined,
            quarantined_legacy_evidence=(),
        )
        return RetiringCacheRepairResult(
            repaired=True,
            state=RetiringCacheState.CORRUPT,
            salvaged=len(salvaged),
            quarantined=len(quarantined),
            sidecar=sidecar,
        )
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
    home: ManagedHome,
    records: tuple[RetiringArtifactRecord, ...],
    legacy_evidence: tuple[LegacyRetiringEvidence, ...],
    quarantined_records: tuple[QuarantinedRetiringRecord, ...] = (),
    quarantined_legacy_evidence: tuple[QuarantinedRetiringRecord, ...] = (),
) -> None:
    write_versioned_json(
        _retiring_cache_path(home),
        {
            "records": [_record_to_json(record) for record in records]
            + [json.loads(item.raw_json) for item in quarantined_records],
            "legacy_evidence": [_legacy_to_json(item) for item in legacy_evidence]
            + [json.loads(item.raw_json) for item in quarantined_legacy_evidence],
        },
        schema_version=_RETIRING_CACHE_SCHEMA_VERSION,
        strict_durability=True,
    )


def _legacy_record_id(version: str, path: str, retired_at: str) -> str:
    payload = "\0".join((version, path, retired_at)).encode()
    return hashlib.sha256(payload).hexdigest()


def is_reclaimable_artifact_path(path: Path, managed_root: Path) -> bool:
    """Return whether *path* may ever be treated as a retirable incarnation.

    Only a direct, non-hidden child of *managed_root* qualifies. Dot-prefixed
    entries are managed infrastructure, never artifacts — most importantly
    ``plugin-projections/.artifact-leases``, the directory holding the lock
    files every live session's inherited reader lease is held on. Reclaiming
    it would delete the lease infrastructure out from under every running
    session.

    Nested descendants are excluded too: an artifact is exactly one level deep,
    so anything deeper is a component of an artifact rather than an artifact.
    """
    return not path.name.startswith(".") and path.parent == managed_root


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
            if not is_reclaimable_artifact_path(location, managed_root):
                return None, "legacy path is managed infrastructure, not an artifact incarnation"
            return kind, None
    return None, "legacy path is outside known managed roots"


def migrate_retiring_cache_v1(
    managed_roots: Mapping[PluginArtifactKind, Path],
    *,
    home: ManagedHome | None = None,
) -> RetiringCacheReadResult:
    """Persist v1 path-only records as non-destructive typed evidence."""
    resolved_home = home if home is not None else managed_home()
    fh = _open_lock(_retiring_cache_lock(resolved_home))
    try:
        state = _read_retiring_cache_unlocked(resolved_home)
        if state.state is not RetiringCacheState.LEGACY_V1:
            return state
        raw = json.loads(_retiring_cache_path(resolved_home).read_text(encoding="utf-8"))
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
        _write_retiring_cache_unlocked(
            resolved_home,
            result.records,
            result.legacy_evidence,
            quarantined_records=(),
            quarantined_legacy_evidence=(),
        )
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
    home: ManagedHome | None = None,
    on_persisted: Callable[[str], None] | None = None,
) -> RetiringAppendResult | None:
    """Append one exact v2 record, preserving first-seen order and intent identity."""
    resolved_home = home if home is not None else managed_home()
    fh = _open_lock(_retiring_cache_lock(resolved_home))
    try:
        state = _read_retiring_cache_unlocked(resolved_home)
        if state.state is RetiringCacheState.ABSENT:
            records: tuple[RetiringArtifactRecord, ...] = ()
            evidence: tuple[LegacyRetiringEvidence, ...] = ()
            quarantined_records: tuple[QuarantinedRetiringRecord, ...] = ()
            quarantined_evidence: tuple[QuarantinedRetiringRecord, ...] = ()
        elif state.state is RetiringCacheState.EXACT_V2:
            records = state.records
            evidence = state.legacy_evidence
            quarantined_records = state.quarantined_records
            quarantined_evidence = state.quarantined_legacy_evidence
        else:
            return None
        intent = _retirement_intent(record)
        for existing in records:
            if existing.record_id == record.record_id and existing != record:
                return None
            if _retirement_intent(existing) == intent:
                return RetiringAppendResult(record_id=existing.record_id, created=False)
        if any(item.record_id == record.record_id for item in evidence):
            return None
        if any(
            isinstance(raw := json.loads(item.raw_json), dict)
            and raw.get("record_id") == record.record_id
            for item in (*quarantined_records, *quarantined_evidence)
        ):
            return None
        try:
            _write_retiring_cache_unlocked(
                resolved_home,
                (*records, record),
                evidence,
                quarantined_records=quarantined_records,
                quarantined_legacy_evidence=quarantined_evidence,
            )
        except _AtomicWriteDurabilityError:
            if on_persisted is not None:
                on_persisted(record.record_id)
            raise
        if on_persisted is not None:
            on_persisted(record.record_id)
        return RetiringAppendResult(record_id=record.record_id, created=True)
    finally:
        fh.close()


def remove_retiring_records(
    record_ids: Iterable[str], *, home: ManagedHome | None = None
) -> int | None:
    """Remove exact records or migrated evidence by stable record ID."""
    ids = frozenset(record_ids)
    if not ids:
        return 0
    resolved_home = home if home is not None else managed_home()
    fh = _open_lock(_retiring_cache_lock(resolved_home))
    try:
        state = _read_retiring_cache_unlocked(resolved_home)
        if state.state is RetiringCacheState.ABSENT:
            return 0
        if state.state is not RetiringCacheState.EXACT_V2:
            return None
        records = tuple(record for record in state.records if record.record_id not in ids)
        evidence = tuple(item for item in state.legacy_evidence if item.record_id not in ids)
        removed = len(state.records) - len(records) + len(state.legacy_evidence) - len(evidence)
        if removed:
            _write_retiring_cache_unlocked(
                resolved_home,
                records,
                evidence,
                quarantined_records=state.quarantined_records,
                quarantined_legacy_evidence=state.quarantined_legacy_evidence,
            )
        return removed
    finally:
        fh.close()


def due_retiring_records(
    now: datetime, *, home: ManagedHome | None = None
) -> tuple[RetiringArtifactRecord, ...] | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("retirement sweep time must be timezone-aware")
    normalized_now = now.astimezone(UTC)
    state = read_retiring_cache(home=home)
    if state.state is RetiringCacheState.ABSENT:
        return ()
    if state.state is not RetiringCacheState.EXACT_V2:
        return None
    return tuple(record for record in state.records if record.not_before <= normalized_now)


class _InstallLock:
    """Exclusive fcntl lock for the autoskillit install critical section."""

    def __init__(self, home: ManagedHome) -> None:
        self._home = home
        self._lock_file: IO[str] | None = None

    def __enter__(self) -> _InstallLock:
        self._lock_file = _open_lock(_install_lock_path(self._home))
        return self

    def __exit__(self, *_: object) -> None:
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None
