"""Disk-backed staleness check cache for recipe contract verification."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from autoskillit.core import _atomic_write, get_logger

logger = get_logger(__name__)


@dataclasses.dataclass
class StalenessEntry:
    recipe_hash: str  # "sha256:<64-hex>" of recipe file bytes at check time
    manifest_version: str  # installed package version (from load_bundled_manifest)
    is_stale: bool  # True if check_contract_staleness returned non-empty list
    triage_result: str | None  # "cosmetic" | "meaningful" | None (not yet triaged)
    checked_at: str  # ISO 8601 UTC timestamp


def compute_recipe_hash(recipe_path: Path) -> str:
    """sha256 of recipe file bytes, returned as 'sha256:<hex>'."""
    return "sha256:" + hashlib.sha256(recipe_path.read_bytes()).hexdigest()


def read_staleness_cache(cache_path: Path, recipe_name: str) -> StalenessEntry | None:
    """Return stored entry for recipe_name or None. Does NOT validate hash/version."""
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        entry_data = data.get(recipe_name)
        if entry_data is None:
            return None
        return StalenessEntry(
            recipe_hash=entry_data["recipe_hash"],
            manifest_version=entry_data["manifest_version"],
            is_stale=entry_data["is_stale"],
            triage_result=entry_data.get("triage_result"),
            checked_at=entry_data["checked_at"],
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None


def write_staleness_cache(cache_path: Path, recipe_name: str, entry: StalenessEntry) -> None:
    """Atomically update entry using _atomic_write. Swallows OSError (best-effort)."""
    try:
        existing: dict = {}
        if cache_path.is_file():
            try:
                existing = json.loads(cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing[recipe_name] = dataclasses.asdict(entry)
        _atomic_write(cache_path, json.dumps(existing, indent=2))
    except OSError as exc:
        logger.warning("staleness_cache_write_failed", recipe_name=recipe_name, exc_info=exc)
