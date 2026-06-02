"""Recipe API cache and staleness globals."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass as _dc
from pathlib import Path
from typing import Any

from autoskillit.core import SessionType, get_logger, pkg_root, session_type

logger = get_logger(__name__)

_PROCESS_START_PKG_MTIME: int | None = None
_STALENESS_LAST_CHECK: float = 0.0
_STALENESS_IS_STALE: bool = False
_STALENESS_CACHES_CLEARED: bool = False
_STALENESS_TTL: float = 30.0
_STALENESS_SCAN_DIRS: tuple[str, ...] = ("recipe", "recipes")
_DEEP_CONTENT_BASELINE: str | None = None
_STALENESS_LOCK = threading.Lock()


@_dc(frozen=True, slots=True)
class _LoadCacheEntry:
    recipe_path: Path
    recipe_mtime: int
    recipe_size: int
    project_dir_mtime: int
    builtin_dir_mtime: int
    pkg_version: str
    rule_registry_hash: str
    result: Any  # LoadRecipeResult but avoiding circular import


class LoadCache:
    """Thread-safe recipe cache with copy-on-read guarantee.

    Encapsulates the lock internally so callers cannot bypass it.
    Provides copy_result() for aliasing-safe access to cached dicts.
    """

    def __init__(self) -> None:
        self._store: dict[tuple, _LoadCacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple) -> _LoadCacheEntry | None:
        with self._lock:
            return self._store.get(key)

    def copy_result(self, result: Any) -> dict[str, Any]:
        """Return a shallow copy of result with list fields independently copied."""
        if not isinstance(result, Mapping):
            msg = f"copy_result expected a Mapping, got {type(result).__name__}"
            raise TypeError(msg)
        r = dict(result)
        for list_key in (
            "suggestions",
            "kitchen_rules",
            "requires_packs",
            "requires_features",
            "deferred_guards",
        ):
            if list_key in r:
                r[list_key] = list(r[list_key])
        return r

    def put(self, key: tuple, entry: _LoadCacheEntry) -> None:
        with self._lock:
            self._store[key] = entry

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_LOAD_CACHE = LoadCache()

_MISSING = object()


class YamlFileCache:
    """Thread-safe single-file cache keyed on (mtime_ns, size).

    Replaces @lru_cache(maxsize=1) for YAML-backed functions.
    Staleness is structurally impossible: the cache key changes
    with the file, forcing a re-read.
    """

    def __init__(self) -> None:
        self._key: tuple[int, int] = (0, 0)
        self._value: Any = _MISSING
        self._lock = threading.Lock()

    def get_or_load(self, path: Path, loader: Callable[[Path], Any]) -> Any:
        mtime = _path_mtime_ns(path)
        size = _file_size(path)
        key = (mtime, size)
        with self._lock:
            if key == self._key and self._value is not _MISSING:
                return self._value
            value = loader(path)
            self._key = key
            self._value = value
            return value

    def clear(self) -> None:
        with self._lock:
            self._key = (0, 0)
            self._value = _MISSING


def _path_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _get_pkg_version() -> str:
    from autoskillit import __version__

    return __version__


def _compute_registry_hash(experiment_types_dir: Path) -> str:
    """Compute md5 hash of sorted YAML file contents for experiment-type registry."""
    if not experiment_types_dir.exists():
        return ""
    h = hashlib.md5(usedforsecurity=False)
    for p in sorted(experiment_types_dir.glob("*.yaml")):
        try:
            h.update(p.name.encode())
            h.update(p.read_bytes())
        except OSError:
            continue
    return h.hexdigest()


def _compute_content_hash() -> str:
    """Return SHA-256 hex digest of all .py/.yaml files in staleness-scanned subdirectories."""
    root = pkg_root()
    h = hashlib.sha256()
    for subdir in _STALENESS_SCAN_DIRS:
        d = root / subdir
        if d.is_dir():
            for f in sorted(
                [*d.rglob("*.py"), *d.rglob("*.yaml")],
                key=lambda p: p.relative_to(root),
            ):
                if f.is_file():
                    try:
                        rel = f.relative_to(root)
                        h.update(f"{rel}\n".encode())
                        h.update(f.read_bytes())
                    except OSError:
                        continue
    return h.hexdigest()


def _get_process_start_mtime() -> int:
    global _PROCESS_START_PKG_MTIME, _DEEP_CONTENT_BASELINE  # noqa: PLW0603
    if _PROCESS_START_PKG_MTIME is None:
        _PROCESS_START_PKG_MTIME = _path_mtime_ns(pkg_root())
        _DEEP_CONTENT_BASELINE = _compute_content_hash()
    return _PROCESS_START_PKG_MTIME


def _check_process_staleness() -> bool:
    """Return True if recipe Python source content changed since process start.

    Fleet sessions always return False — dispatched subprocesses have fresh
    baselines and revalidate independently.
    """
    if session_type() is SessionType.FLEET:
        _get_process_start_mtime()
        return False

    global _STALENESS_LAST_CHECK, _STALENESS_IS_STALE  # noqa: PLW0603
    now = time.monotonic()
    if now - _STALENESS_LAST_CHECK < _STALENESS_TTL:
        return _STALENESS_IS_STALE
    with _STALENESS_LOCK:
        _STALENESS_LAST_CHECK = now
        try:
            _get_process_start_mtime()
            current_hash = _compute_content_hash()
            pkg_stale = current_hash != _DEEP_CONTENT_BASELINE
            _STALENESS_IS_STALE = pkg_stale
        except (OSError, RuntimeError):
            logger.warning("pkg_root() unavailable during staleness check; assuming non-stale")
            _STALENESS_IS_STALE = False
    return _STALENESS_IS_STALE


def _refresh_staleness_baseline() -> None:
    """Re-capture baselines after a confirmed-good load."""
    global _PROCESS_START_PKG_MTIME, _DEEP_CONTENT_BASELINE  # noqa: PLW0603
    global _STALENESS_LAST_CHECK, _STALENESS_IS_STALE  # noqa: PLW0603
    with _STALENESS_LOCK:
        _PROCESS_START_PKG_MTIME = _path_mtime_ns(pkg_root())
        _DEEP_CONTENT_BASELINE = _compute_content_hash()
        _STALENESS_LAST_CHECK = 0.0
        _STALENESS_IS_STALE = False


def _clear_stale_caches() -> None:
    """Clear all caches and the load cache when staleness is detected."""
    global _STALENESS_CACHES_CLEARED  # noqa: PLW0603
    from autoskillit.recipe._contracts_manifest import _MANIFEST_CACHE  # noqa: PLC0415
    from autoskillit.recipe._skill_helpers import (  # noqa: PLC0415
        _SKILL_CATEGORY_CACHE,
        _SKILL_NAMES_CACHE,
    )
    from autoskillit.recipe.methodology_venue_appendix import (  # noqa: PLC0415
        _ML_SUB_AREA_CACHE,
    )
    from autoskillit.recipe.rules.rules_blocks import _BUDGETS_CACHE  # noqa: PLC0415

    _BUDGETS_CACHE.clear()
    _MANIFEST_CACHE.clear()
    _ML_SUB_AREA_CACHE.clear()
    _SKILL_NAMES_CACHE.clear()
    _SKILL_CATEGORY_CACHE.clear()
    _LOAD_CACHE.clear()
    _STALENESS_CACHES_CLEARED = True
