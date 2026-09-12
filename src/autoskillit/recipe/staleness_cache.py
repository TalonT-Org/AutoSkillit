"""Backward-compat shim for ``recipe/staleness_cache.py``.

Real implementation: ``autoskillit.recipe.contracts.staleness_cache`` (#4671 D).
Preserves old import path ``autoskillit.recipe.staleness_cache``.
"""

from __future__ import annotations

from autoskillit.recipe.contracts.staleness_cache import (
    Path,
    StalenessEntry,
    annotations,
    atomic_write,
    compute_recipe_hash,
    get_logger,
    logger,
    read_staleness_cache,
    write_staleness_cache,
)

__all__ = [
    "Path",
    "StalenessEntry",
    "annotations",
    "atomic_write",
    "compute_recipe_hash",
    "get_logger",
    "logger",
    "read_staleness_cache",
    "write_staleness_cache",
]
