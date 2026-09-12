"""Backward-compat shim for ``recipe/_api_cache.py``.

Real implementation: ``autoskillit.recipe.api._api_cache`` (#4951).
Preserves old import path ``autoskillit.recipe._api_cache``.
"""

from __future__ import annotations

from autoskillit.recipe.api._api_cache import (  # noqa: F401
    _LOAD_CACHE,
    _MISSING,
    _STALENESS_CACHES_CLEARED,
    Any,
    Callable,
    LoadCache,
    Mapping,
    Path,
    SessionType,
    YamlFileCache,
    _check_process_staleness,
    _clear_stale_caches,
    _compute_registry_hash,
    _LoadCacheEntry,
    _path_mtime_ns,
    _refresh_staleness_baseline,
    annotations,
    get_logger,
    hashlib,
    logger,
    pkg_root,
    session_type,
    threading,
    time,
)
