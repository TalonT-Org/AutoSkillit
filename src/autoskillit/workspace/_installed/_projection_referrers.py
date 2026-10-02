"""Session-home referrers that pin a projected plugin root against reclamation.

A per-session backend home bakes absolute hook and script paths inside the
projection it was materialized from. The projection reclaimer consults these
records, so a projection is never deleted while a home that bakes it survives.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from autoskillit.core import (
    SESSION_STALE_SECONDS,
    destination_location,
    get_logger,
    read_versioned_json,
    safe_mtime,
    write_versioned_json,
)

logger = get_logger(__name__)

__all__ = [
    "REFERRER_DIRECTORY_NAME",
    "live_projected_artifact_referrers",
    "projected_artifact_referrer_dir",
    "record_projected_artifact_referrer",
]

_PROJECTIONS_DIRECTORY_NAME = "plugin-projections"
REFERRER_DIRECTORY_NAME = ".artifact-referrers"
_REFERRER_SCHEMA_VERSION = 1


def projected_artifact_referrer_dir(managed_path: Path) -> Path:
    """Return the directory of session-home referrers that pin a projected root."""
    managed_path = Path(managed_path)
    return managed_path.parent / REFERRER_DIRECTORY_NAME / managed_path.name


def _live_referrer_home(referrer: Path, now: float) -> Path | None:
    """Return the home a referrer pins, or ``None`` once it can no longer pin.

    A referrer is live while its home would survive ``cleanup_stale``. One that
    cannot be read pins by its own freshness, so a torn write never un-pins.
    """
    payload = read_versioned_json(referrer, _REFERRER_SCHEMA_VERSION, logger=logger)
    recorded_home = payload.get("home") if payload is not None else None
    if not isinstance(recorded_home, str):
        referrer_mtime = safe_mtime(referrer)
        if referrer_mtime is not None and now - referrer_mtime <= SESSION_STALE_SECONDS:
            return referrer
        return None
    home = Path(recorded_home)
    home_mtime = safe_mtime(home)
    if home_mtime is None or now - home_mtime > SESSION_STALE_SECONDS:
        return None
    return home


def live_projected_artifact_referrers(managed_path: Path) -> tuple[Path, ...]:
    """Return the session homes still baking a projected root, pruning the rest.

    Past ``SESSION_STALE_SECONDS`` a home no longer pins: a leased session still
    holds the projection's shared lease, and an unleased home that old is one
    ``cleanup_stale`` deletes. That bounds how long an orphaned home in a project
    that is never reopened can pin its projection.
    """
    referrer_dir = projected_artifact_referrer_dir(managed_path)
    try:
        referrers = sorted(referrer_dir.iterdir())
    except FileNotFoundError:
        return ()
    except OSError:
        return (referrer_dir,)
    now = time.time()
    homes: list[Path] = []
    for referrer in referrers:
        home = _live_referrer_home(referrer, now)
        if home is None:
            referrer.unlink(missing_ok=True)
            continue
        homes.append(home)
    return tuple(homes)


def record_projected_artifact_referrer(managed_path: Path, home: Path) -> None:
    """Pin a projected root for as long as *home* bakes paths inside it."""
    location = destination_location(Path(managed_path))
    projections_root = location.parent
    if (
        projections_root.name != _PROJECTIONS_DIRECTORY_NAME
        or projections_root.parent.name != ".autoskillit"
    ):
        raise ValueError(f"session hook root is not a projected plugin artifact: {managed_path}")
    canonical_home = os.path.realpath(home)
    live_projected_artifact_referrers(managed_path)
    referrer_name = hashlib.sha256(canonical_home.encode("utf-8")).hexdigest()[:24]
    write_versioned_json(
        projected_artifact_referrer_dir(managed_path) / f"{referrer_name}.json",
        {"home": canonical_home},
        _REFERRER_SCHEMA_VERSION,
    )
