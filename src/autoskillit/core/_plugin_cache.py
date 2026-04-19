"""Plugin cache lifecycle: retiring cache, install locking, kitchen registry."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

import psutil

from .io import atomic_write, write_versioned_json
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
    except BaseException:
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
            try:
                entries = json.loads(cache.read_text()).get("retiring", [])
            except (json.JSONDecodeError, AttributeError):
                entries = []
        entries.append({"version": version, "path": path, "retired_at": datetime.now(UTC).isoformat()})
        write_versioned_json(cache, {"retiring": entries}, schema_version=_SCHEMA_VERSION)
    finally:
        fh.close()


def sweep_retiring_cache(grace_hours: int = 2) -> int:
    cache = _retiring_cache_path()
    lock = _retiring_cache_lock()
    if not cache.exists():
        return 0
    fh = _open_lock(lock)
    try:
        try:
            data = json.loads(cache.read_text())
            entries: list[dict[str, str]] = data.get("retiring", [])
        except (json.JSONDecodeError, AttributeError, OSError):
            return 0

        survivors: list[dict[str, str]] = []
        count = 0
        cutoff = timedelta(hours=grace_hours)
        for entry in entries:
            retired_at_str = entry.get("retired_at")
            if not retired_at_str:
                continue
            try:
                retired_at = datetime.fromisoformat(retired_at_str)
                age = datetime.now(UTC) - retired_at
            except (ValueError, TypeError):
                continue
            if age >= cutoff:
                path = entry.get("path", "")
                if path and Path(path).is_dir():
                    try:
                        shutil.rmtree(path)
                    except OSError:
                        survivors.append(entry)
                        continue
                count += 1
            else:
                survivors.append(entry)

        write_versioned_json(cache, {"retiring": survivors}, schema_version=_SCHEMA_VERSION)
        return count
    finally:
        fh.close()


def _retire_old_versions(cache_dir: Path, new_version: str) -> None:
    for subdir in list(cache_dir.iterdir()):
        if not subdir.is_dir():
            continue
        if subdir.name == new_version:
            shutil.rmtree(subdir)
        else:
            append_retiring_entry(version=subdir.name, path=str(subdir))
    sweep_retiring_cache()


class _InstallLock:
    """Exclusive fcntl lock for the autoskillit install critical section."""

    def __init__(self) -> None:
        self._lock_file: IO[str] | None = None

    def __enter__(self) -> "_InstallLock":
        self._lock_file = _open_lock(_install_lock_path())
        return self

    def __exit__(self, *_: object) -> None:
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None


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
            try:
                entries = json.loads(akp.read_text()).get("kitchens", [])
            except (json.JSONDecodeError, AttributeError):
                entries = []
        try:
            create_time: float | None = psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:
            create_time = None
        entries.append({
            "kitchen_id": kitchen_id,
            "pid": pid,
            "create_time": create_time,
            "project_path": project_path,
            "opened_at": datetime.now(UTC).isoformat(),
        })
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
            try:
                entries = json.loads(akp.read_text()).get("kitchens", [])
            except (json.JSONDecodeError, AttributeError):
                entries = []
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
            try:
                entries = json.loads(akp.read_text()).get("kitchens", [])
            except (json.JSONDecodeError, AttributeError):
                entries = []
        survivors = [e for e in entries if e.get("pid") != pid]
        write_versioned_json(akp, {"kitchens": survivors}, schema_version=_SCHEMA_VERSION)
    finally:
        fh.close()


def any_kitchen_open() -> bool:
    akp = _active_kitchens_path()
    lock = _active_kitchens_lock()
    if not akp.exists():
        return False
    fh = _open_lock(lock)
    try:
        try:
            entries: list[dict[str, object]] = json.loads(akp.read_text()).get("kitchens", [])
        except (json.JSONDecodeError, AttributeError, OSError):
            return False
        survivors = []
        for entry in entries:
            pid = entry.get("pid")
            if not isinstance(pid, int):
                continue
            create_time = entry.get("create_time")
            stored: float | None = float(create_time) if isinstance(create_time, (int, float)) else None
            if _pid_alive(pid, stored_create_time=stored):
                survivors.append(entry)
        write_versioned_json(akp, {"kitchens": survivors}, schema_version=_SCHEMA_VERSION)
        return len(survivors) > 0
    finally:
        fh.close()
