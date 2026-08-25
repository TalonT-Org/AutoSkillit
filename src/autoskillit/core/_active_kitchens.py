"""Active-kitchen registry, liveness checks, and process tracking.

The active-kitchen registry uses its own lock (``_active_kitchens_lock``) on
its own file (``_active_kitchens_path``). The liveness primitives
(``_pid_alive``, ``_check_pid_with_psutil``) are colocated here because they
are only consumed by ``kitchen_entry_alive`` inside this module — keeping
them adjacent preserves the
``test_no_raw_zombie_blind_liveness_check_outside_shared_primitive``
invariant from ``tests/arch/test_ast_rules.py``.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import psutil

from ._retiring_cache import _open_lock, _parse_utc
from .io import write_versioned_json
from .logging import get_logger
from .types import ManagedHome, managed_home

logger = get_logger(__name__)

_ACTIVE_KITCHENS_SCHEMA_VERSION = 2
_ACTIVE_KITCHEN_FIELDS = frozenset(
    {"kitchen_id", "pid", "create_time", "project_path", "opened_at"}
)


def _active_kitchens_path(home: ManagedHome) -> Path:
    return home.autoskillit_dir / "active_kitchens.json"


def _active_kitchens_lock(home: ManagedHome) -> Path:
    return home.autoskillit_dir / "active_kitchens.lock"


class ActiveKitchensState(StrEnum):
    """Safety classification for the active-kitchens registry."""

    EXACT = "exact"
    CORRUPT = "corrupt"
    UNSUPPORTED_FUTURE = "unsupported_future"


@dataclass(frozen=True, slots=True)
class ActiveKitchensReadResult:
    """Classified active-kitchens read without unsafe empty-list collapse."""

    state: ActiveKitchensState
    entries: tuple[dict[str, object], ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class KitchenProcessIdentity:
    kitchen_id: str
    pid: int
    create_time: float
    project_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.kitchen_id, str) or not self.kitchen_id:
            raise ValueError("kitchen_id must be a nonempty string")
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise ValueError("pid must be a positive integer")
        if (
            isinstance(self.create_time, bool)
            or not isinstance(self.create_time, (int, float))
            or not math.isfinite(self.create_time)
            or self.create_time <= 0
        ):
            raise ValueError("create_time must be a finite positive number")
        if not isinstance(self.project_path, str) or not self.project_path:
            raise ValueError("project_path must be a nonempty string")


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
        raise ValueError("active kitchen entry does not match the supported schema")
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
        or not math.isfinite(create_time)
        or create_time <= 0
    ):
        raise ValueError("active kitchen create_time must be a positive number")
    if not isinstance(project_path, str) or not project_path:
        raise ValueError("active kitchen project_path must be nonempty")
    _parse_utc(opened_at, field_name="active kitchen opened_at")
    return KitchenProcessIdentity(kitchen_id, pid, float(create_time), project_path)


def _active_kitchens_corrupt(
    path: Path,
    error: object,
    *,
    entries: tuple[dict[str, object], ...] = (),
) -> ActiveKitchensReadResult:
    logger.warning(
        "active_kitchens_registry_corrupt",
        path=str(path),
        error=str(error),
    )
    return ActiveKitchensReadResult(
        state=ActiveKitchensState.CORRUPT,
        entries=entries,
        error=str(error),
    )


def _read_active_kitchens_unlocked(path: Path) -> ActiveKitchensReadResult:
    try:
        if not path.exists():
            return ActiveKitchensReadResult(state=ActiveKitchensState.EXACT)
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _active_kitchens_corrupt(path, exc)
    if not isinstance(raw, dict):
        return _active_kitchens_corrupt(path, "active kitchen registry root must be an object")
    schema_version = raw.get("schema_version")
    if type(schema_version) is int and schema_version > _ACTIVE_KITCHENS_SCHEMA_VERSION:
        return ActiveKitchensReadResult(
            state=ActiveKitchensState.UNSUPPORTED_FUTURE,
            error=f"active kitchen registry schema {schema_version} is unsupported",
        )
    if frozenset(raw) != {"schema_version", "kitchens"}:
        return _active_kitchens_corrupt(
            path, "active kitchen registry does not match the supported schema"
        )
    if type(schema_version) is not int or schema_version not in {
        1,
        _ACTIVE_KITCHENS_SCHEMA_VERSION,
    }:
        return _active_kitchens_corrupt(path, "active kitchen registry schema is malformed")
    kitchens = raw.get("kitchens")
    if not isinstance(kitchens, list):
        return _active_kitchens_corrupt(path, "active kitchen registry kitchens must be a list")
    entries: list[dict[str, object]] = []
    malformed_v2 = False
    for entry in kitchens:
        try:
            _identity_from_entry(entry)
        except (TypeError, ValueError, OverflowError):
            if schema_version == 1:
                continue
            malformed_v2 = True
            continue
        entries.append(dict(entry))
    frozen_entries = tuple(entries)
    if malformed_v2:
        return _active_kitchens_corrupt(
            path,
            "active kitchen registry contains malformed v2 entries",
            entries=frozen_entries,
        )
    return ActiveKitchensReadResult(
        state=ActiveKitchensState.EXACT,
        entries=frozen_entries,
    )


def kitchen_entry_alive(entry: dict) -> bool:
    """Return True if an active_kitchens.json entry's process is still running."""
    try:
        identity = _identity_from_entry(entry)
    except ValueError:
        return False
    return _pid_alive(identity.pid, stored_create_time=identity.create_time)


def read_active_kitchens_registry(*, home: ManagedHome | None = None) -> ActiveKitchensReadResult:
    """Return a classified, locked read of the active-kitchens registry.

    Public counterpart to the private ``_active_kitchens_path``/``_active_kitchens_lock``
    pair — callers outside this module must not reach into private submodule internals
    (REQ-ARCH-001), so this is the sanctioned read surface for registry consumers such
    as ``prune_stale_kitchen_state``.
    """
    try:
        resolved_home = home if home is not None else managed_home()
        akp = _active_kitchens_path(resolved_home)
        lock = _active_kitchens_lock(resolved_home)
        fh = _open_lock(lock)
        try:
            return _read_active_kitchens_unlocked(akp)
        finally:
            fh.close()
    except Exception as exc:
        logger.warning("active_kitchens_registry_read_failed", error=str(exc), exc_info=True)
        return ActiveKitchensReadResult(
            state=ActiveKitchensState.CORRUPT,
            error=str(exc),
        )


def _pid_alive(pid: int, stored_create_time: float | None = None) -> bool:
    # Allowlisted in test_no_raw_zombie_blind_liveness_check_outside_shared_primitive.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return _check_pid_with_psutil(pid, stored_create_time)
    return _check_pid_with_psutil(pid, stored_create_time)


def _check_pid_with_psutil(pid: int, stored_create_time: float | None) -> bool:
    """Post-probe liveness check via psutil — assumes os.kill(pid, 0) already succeeded.

    Reports False only when psutil definitively confirms the process is gone (NoSuchProcess)
    or in a terminal zombie/dead state. Foreign-user PIDs (AccessDenied) are unverifiable,
    so we assume alive to avoid retiring a live kitchen on a transient psutil failure.
    """
    try:
        proc = psutil.Process(pid)
        if stored_create_time is not None:
            identity_match = abs(proc.create_time() - stored_create_time) < 1.0
        else:
            identity_match = True
        return identity_match and proc.status() not in (
            psutil.STATUS_ZOMBIE,
            psutil.STATUS_DEAD,
        )
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def register_active_kitchen(
    identity: KitchenProcessIdentity, *, home: ManagedHome | None = None
) -> bool:
    try:
        resolved_home = home if home is not None else managed_home()
        lock = _active_kitchens_lock(resolved_home)
        akp = _active_kitchens_path(resolved_home)
        fh = _open_lock(lock)
        try:
            read_result = _read_active_kitchens_unlocked(akp)
            if read_result.state is ActiveKitchensState.UNSUPPORTED_FUTURE:
                return False
            entries = [
                entry for entry in read_result.entries if _identity_from_entry(entry) != identity
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
            return True
        finally:
            fh.close()
    except Exception as exc:
        logger.warning("active_kitchen_register_failed", error=str(exc), exc_info=True)
        return False


def unregister_active_kitchen(
    identity: KitchenProcessIdentity, *, home: ManagedHome | None = None
) -> bool:
    try:
        resolved_home = home if home is not None else managed_home()
        lock = _active_kitchens_lock(resolved_home)
        akp = _active_kitchens_path(resolved_home)
        fh = _open_lock(lock)
        try:
            read_result = _read_active_kitchens_unlocked(akp)
            if read_result.state is ActiveKitchensState.UNSUPPORTED_FUTURE:
                return False
            survivors = [
                entry for entry in read_result.entries if _identity_from_entry(entry) != identity
            ]
            write_versioned_json(
                akp,
                {"kitchens": survivors},
                schema_version=_ACTIVE_KITCHENS_SCHEMA_VERSION,
            )
            return True
        finally:
            fh.close()
    except Exception as exc:
        logger.warning("active_kitchen_unregister_failed", error=str(exc), exc_info=True)
        return False


def any_kitchen_open(project_path: str | None = None, *, home: ManagedHome | None = None) -> bool:
    try:
        resolved_home = home if home is not None else managed_home()
        akp = _active_kitchens_path(resolved_home)
        lock = _active_kitchens_lock(resolved_home)
        if not akp.exists():
            return False
        fh = _open_lock(lock)
        try:
            read_result = _read_active_kitchens_unlocked(akp)
            if read_result.state is not ActiveKitchensState.EXACT:
                return True
            survivors = [entry for entry in read_result.entries if kitchen_entry_alive(entry)]
            if project_path is not None:
                canonical_project = str(Path(project_path).resolve(strict=False))
                return any(entry.get("project_path") == canonical_project for entry in survivors)
            return len(survivors) > 0
        finally:
            fh.close()
    except Exception as exc:
        logger.warning(
            "active_kitchen_open_check_failed",
            error=str(exc),
            exc_info=True,
        )
        return True
