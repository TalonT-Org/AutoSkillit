"""Plugin cache lifecycle: retiring cache, install locking, kitchen registry."""

from __future__ import annotations

import fcntl
import os
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

import psutil

from .io import read_versioned_json, write_versioned_json
from .logging import get_logger

logger = get_logger(__name__)

_SCHEMA_VERSION = 1


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


def append_retiring_entry(version: str, path: str) -> None:
    lock = _retiring_cache_lock()
    cache = _retiring_cache_path()
    fh = _open_lock(lock)
    try:
        entries: list[dict[str, str]] = []
        if cache.exists():
            data = read_versioned_json(cache, _SCHEMA_VERSION, logger=logger)
            entries = data.get("retiring", []) if data is not None else []
        entries.append(
            {"version": version, "path": path, "retired_at": datetime.now(UTC).isoformat()}
        )
        write_versioned_json(cache, {"retiring": entries}, schema_version=_SCHEMA_VERSION)
    finally:
        fh.close()


#: Hard ceiling on how long a registry-referenced retiring entry may be deferred.
#: Past this age the directory is deleted anyway and the inconsistency is reported
#: by ``verify_install_state()`` — the registry, not the sweeper, is the thing that
#: is wrong. Without a ceiling a stale registry entry defers its directory forever
#: (``retired_at`` is stamped once, so every later sweep re-defers it unchanged),
#: trading a premature-delete bug for an unbounded-retention one.
MAX_DEFER_HOURS = 72


def sweep_retiring_cache(grace_hours: int = 2, max_defer_hours: int = MAX_DEFER_HOURS) -> int:
    """Delete aged-out retiring cache directories; defer registry-referenced ones.

    A directory still named by ``installed_plugins.json`` is *deferred* rather
    than deleted, so our own sweeper can never be the thing that turns a live
    registry entry into a dangling pointer. The defer is bounded by
    ``max_defer_hours``; past that the directory goes and the drift is surfaced
    as an operator-actionable finding instead. Deleting past the ceiling is safe
    because no execution path resolves a plugin source from that path any more.
    """
    cache = _retiring_cache_path()
    lock = _retiring_cache_lock()
    if not cache.exists():
        return 0
    fh = _open_lock(lock)
    try:
        data = read_versioned_json(cache, _SCHEMA_VERSION, logger=logger)
        if data is None:
            return 0
        entries: list[dict[str, str]] = data.get("retiring", [])

        from ._plugin_ids import registered_install_paths

        registered = {str(Path(p)) for p in registered_install_paths()}

        survivors: list[dict[str, str]] = []
        count = 0
        cutoff = timedelta(hours=grace_hours)
        defer_ceiling = timedelta(hours=max_defer_hours)
        for entry in entries:
            retired_at_str = entry.get("retired_at")
            if not retired_at_str:
                survivors.append(entry)
                continue
            try:
                retired_at = datetime.fromisoformat(retired_at_str)
                age = datetime.now(UTC) - retired_at
            except (ValueError, TypeError):
                survivors.append(entry)
                continue
            if age < cutoff:
                survivors.append(entry)
                continue
            path = entry.get("path", "")
            if path and str(Path(path)) in registered and age < defer_ceiling:
                logger.info(
                    "sweep_retiring_cache: deferring %s — still referenced by "
                    "installed_plugins.json (age %.1fh, ceiling %dh)",
                    path,
                    age.total_seconds() / 3600.0,
                    max_defer_hours,
                )
                survivors.append(entry)
                continue
            if path and Path(path).is_dir():
                try:
                    shutil.rmtree(path)
                except OSError as exc:
                    logger.warning("sweep_retiring_cache: failed to remove %s: %s", path, exc)
                    survivors.append(entry)
                    continue
            count += 1

        write_versioned_json(cache, {"retiring": survivors}, schema_version=_SCHEMA_VERSION)
        return count
    finally:
        fh.close()


def retiring_cache_entries() -> tuple[dict[str, str], ...]:
    """Return the current retiring-cache entries (locked read), for diagnostics."""
    cache = _retiring_cache_path()
    if not cache.exists():
        return ()
    fh = _open_lock(_retiring_cache_lock())
    try:
        data = read_versioned_json(cache, _SCHEMA_VERSION, logger=logger)
        if data is None:
            return ()
        return tuple(data.get("retiring", []))
    finally:
        fh.close()


def drop_retiring_entries(paths: Iterable[str]) -> int:
    """Remove retiring entries naming any of *paths*; return the number dropped.

    The rollback half of ``install()``'s transaction: a failed install must not
    leave the live cache queued for deletion.
    """
    targets = {str(Path(p)) for p in paths}
    if not targets:
        return 0
    cache = _retiring_cache_path()
    if not cache.exists():
        return 0
    fh = _open_lock(_retiring_cache_lock())
    try:
        data = read_versioned_json(cache, _SCHEMA_VERSION, logger=logger)
        if data is None:
            return 0
        entries: list[dict[str, str]] = data.get("retiring", [])
        survivors = [e for e in entries if str(Path(e.get("path", ""))) not in targets]
        dropped = len(entries) - len(survivors)
        if dropped:
            write_versioned_json(cache, {"retiring": survivors}, schema_version=_SCHEMA_VERSION)
        return dropped
    finally:
        fh.close()


def _retire_old_versions(cache_dir: Path, new_version: str) -> None:
    for subdir in list(cache_dir.iterdir()):
        if not subdir.is_dir():
            continue
        if subdir.name == new_version:
            try:
                shutil.rmtree(subdir)
            except OSError as exc:
                logger.warning(
                    "_retire_old_versions: failed to remove same-version dir %s: %s", subdir, exc
                )
        else:
            append_retiring_entry(version=subdir.name, path=str(subdir))
    sweep_retiring_cache()


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
        data = read_versioned_json(akp, _SCHEMA_VERSION, logger=logger)
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
            data = read_versioned_json(akp, _SCHEMA_VERSION, logger=logger)
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
        write_versioned_json(akp, {"kitchens": entries}, schema_version=_SCHEMA_VERSION)
    finally:
        fh.close()


def unregister_active_kitchen(kitchen_id: str) -> None:
    lock = _active_kitchens_lock()
    akp = _active_kitchens_path()
    fh = _open_lock(lock)
    try:
        entries: list[dict[str, object]] = []
        if akp.exists():
            data = read_versioned_json(akp, _SCHEMA_VERSION, logger=logger)
            entries = data.get("kitchens", []) if data is not None else []
        survivors = [e for e in entries if e.get("kitchen_id") != kitchen_id]
        write_versioned_json(akp, {"kitchens": survivors}, schema_version=_SCHEMA_VERSION)
    finally:
        fh.close()


def clear_kitchens_for_pid(pid: int) -> None:
    lock = _active_kitchens_lock()
    akp = _active_kitchens_path()
    fh = _open_lock(lock)
    try:
        entries: list[dict[str, object]] = []
        if akp.exists():
            data = read_versioned_json(akp, _SCHEMA_VERSION, logger=logger)
            entries = data.get("kitchens", []) if data is not None else []
        survivors = [e for e in entries if e.get("pid") != pid]
        write_versioned_json(akp, {"kitchens": survivors}, schema_version=_SCHEMA_VERSION)
    finally:
        fh.close()


def any_kitchen_open(project_path: str | None = None) -> bool:
    akp = _active_kitchens_path()
    lock = _active_kitchens_lock()
    if not akp.exists():
        return False
    fh = _open_lock(lock)
    try:
        data = read_versioned_json(akp, _SCHEMA_VERSION, logger=logger)
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
                write_versioned_json(akp, {"kitchens": survivors}, schema_version=_SCHEMA_VERSION)
            except OSError as exc:
                logger.warning("any_kitchen_open: failed to persist pruned kitchens: %s", exc)
        if project_path is not None:
            return any(entry.get("project_path") == project_path for entry in survivors)
        return len(survivors) > 0
    finally:
        fh.close()
