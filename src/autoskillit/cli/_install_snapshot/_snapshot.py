"""Path authority for the shared GitHub fetch cache."""

from pathlib import Path

_FETCH_CACHE_FILE = "github_fetch_cache.json"


def _fetch_cache_path(home: Path) -> Path:
    return home / ".autoskillit" / _FETCH_CACHE_FILE
