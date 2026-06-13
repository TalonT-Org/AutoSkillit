"""Versioned probe result cache with 24-hour TTL.

IL-1 module: imports only stdlib and `autoskillit.core`. Stores a per-cli-version
`ProbeResult` keyed by `cli_version`, surviving across runs while staying
under `PROBE_CACHE_TTL`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autoskillit.core import get_logger, read_versioned_json, write_versioned_json

logger = get_logger(__name__)

PROBE_CACHE_TTL: timedelta = timedelta(hours=24)
_SCHEMA_VERSION: int = 1


@dataclass(frozen=True, slots=True)
class ProbeResult:
    cli_version: str
    passed: bool
    failure_detail: str | None
    probe_timestamp: str


def read_probe_cache(cache_path: Path, cli_version: str) -> ProbeResult | None:
    raw = read_versioned_json(cache_path, _SCHEMA_VERSION, logger=logger)
    if raw is None:
        return None
    entries: dict[str, dict] = raw.get("entries", {})
    entry = entries.get(cli_version)
    if entry is None:
        return None
    try:
        ts = datetime.fromisoformat(entry["probe_timestamp"])
        if (datetime.now(UTC) - ts) > PROBE_CACHE_TTL:
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return ProbeResult(
        cli_version=cli_version,
        passed=entry.get("passed", False),
        failure_detail=entry.get("failure_detail"),
        probe_timestamp=entry["probe_timestamp"],
    )


def write_probe_cache(cache_path: Path, result: ProbeResult) -> None:
    try:
        existing = read_versioned_json(cache_path, _SCHEMA_VERSION, logger=logger)
        entries: dict[str, dict] = (existing or {}).get("entries", {})
        entries[result.cli_version] = {
            "passed": result.passed,
            "failure_detail": result.failure_detail,
            "probe_timestamp": result.probe_timestamp,
        }
        write_versioned_json(cache_path, {"entries": entries}, _SCHEMA_VERSION)
    except OSError:
        logger.debug("probe_cache_write_failed", path=str(cache_path), exc_info=True)
