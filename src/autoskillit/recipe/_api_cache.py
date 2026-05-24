"""Recipe API cache and staleness globals."""

from __future__ import annotations

import hashlib
import threading
import time
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
_STALENESS_SCAN_DIRS: tuple[str, ...] = ("recipe/rules",)
_DEEP_MTIME_BASELINE: int | None = None


@_dc
class _LoadCacheEntry:
    recipe_path: Path
    recipe_mtime: int
    recipe_size: int
    project_dir_mtime: int
    builtin_dir_mtime: int
    pkg_version: str
    rule_registry_hash: str
    result: Any  # LoadRecipeResult but avoiding circular import


_LOAD_CACHE: dict[tuple, _LoadCacheEntry] = {}
_LOAD_CACHE_LOCK = threading.Lock()


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
    """Compute md5 hash of sorted (path, mtime_ns) pairs for experiment-type YAMLs."""
    if not experiment_types_dir.exists():
        return ""
    entries: list[tuple[str, int]] = []
    for p in sorted(experiment_types_dir.glob("*.yaml")):
        try:
            entries.append((p.name, p.stat().st_mtime_ns))
        except OSError:
            continue
    return hashlib.md5(str(entries).encode(), usedforsecurity=False).hexdigest()


def _compute_deep_mtime() -> int:
    """Return max mtime_ns across all .py files in staleness-scanned subdirectories."""
    root = pkg_root()
    max_mt = 0
    for subdir in _STALENESS_SCAN_DIRS:
        d = root / subdir
        if d.is_dir():
            for f in d.iterdir():
                if f.suffix == ".py" and f.is_file():
                    try:
                        mt = f.stat().st_mtime_ns
                        if mt > max_mt:
                            max_mt = mt
                    except OSError:
                        continue
    return max_mt


def _get_process_start_mtime() -> int:
    global _PROCESS_START_PKG_MTIME, _DEEP_MTIME_BASELINE  # noqa: PLW0603
    if _PROCESS_START_PKG_MTIME is None:
        _PROCESS_START_PKG_MTIME = _path_mtime_ns(pkg_root())
        _DEEP_MTIME_BASELINE = _compute_deep_mtime()
    return _PROCESS_START_PKG_MTIME


def _check_process_staleness() -> bool:
    """Return True if the package directory or rule files were modified after process start.

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
    _STALENESS_LAST_CHECK = now
    try:
        pkg_stale = _path_mtime_ns(pkg_root()) != _get_process_start_mtime()
        if not pkg_stale:
            current_deep = _compute_deep_mtime()
            pkg_stale = current_deep != _DEEP_MTIME_BASELINE
        _STALENESS_IS_STALE = pkg_stale
    except (OSError, RuntimeError):
        logger.warning("pkg_root() unavailable during staleness check; assuming non-stale")
        _STALENESS_IS_STALE = False
    return _STALENESS_IS_STALE


def _clear_stale_caches() -> None:
    """Clear all lru_cache helpers and the load cache when staleness is detected."""
    global _STALENESS_CACHES_CLEARED  # noqa: PLW0603
    from autoskillit.recipe.contracts import load_bundled_manifest  # noqa: PLC0415
    from autoskillit.recipe.methodology_venue_appendix import (  # noqa: PLC0415
        load_ml_sub_area_folding,
    )
    from autoskillit.recipe.rules.rules_blocks import _block_budgets  # noqa: PLC0415

    _block_budgets.cache_clear()
    load_bundled_manifest.cache_clear()
    load_ml_sub_area_folding.cache_clear()
    with _LOAD_CACHE_LOCK:
        _LOAD_CACHE.clear()
    _STALENESS_CACHES_CLEARED = True
