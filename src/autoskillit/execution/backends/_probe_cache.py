"""Versioned probe result cache with 24-hour TTL.

IL-1 module: imports only stdlib and `autoskillit.core`. Stores a per-cli-version
`ProbeResult` keyed by `cli_version` and validated against the current output
discipline policy, surviving across runs while staying under `PROBE_CACHE_TTL`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autoskillit.core import (
    OUTPUT_DISCIPLINE_BLOCK_SHA256,
    OUTPUT_DISCIPLINE_POLICY_VERSION,
    get_logger,
    read_versioned_json,
    write_versioned_json,
)

logger = get_logger(__name__)

PROBE_CACHE_TTL: timedelta = timedelta(hours=24)
PROBE_POLICY_IDENTITY: str = (
    f"v{OUTPUT_DISCIPLINE_POLICY_VERSION}-{OUTPUT_DISCIPLINE_BLOCK_SHA256}"
)
_SCHEMA_VERSION: int = 2


@dataclass(frozen=True, slots=True)
class ProbeResult:
    cli_version: str
    policy_identity: str
    passed: bool
    failure_detail: str | None
    probe_timestamp: str


def read_probe_cache(
    cache_path: Path,
    cli_version: str,
    policy_identity: str,
) -> ProbeResult | None:
    raw = read_versioned_json(cache_path, _SCHEMA_VERSION, logger=logger)
    if raw is None:
        return None
    entries: dict[str, dict] = raw.get("entries", {})
    entry = entries.get(cli_version)
    if entry is None:
        return None
    if entry.get("policy_identity") != policy_identity:
        return None
    try:
        ts = datetime.fromisoformat(entry["probe_timestamp"])
        if (datetime.now(UTC) - ts) > PROBE_CACHE_TTL:
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return ProbeResult(
        cli_version=cli_version,
        policy_identity=policy_identity,
        passed=entry.get("passed", False),
        failure_detail=entry.get("failure_detail"),
        probe_timestamp=entry["probe_timestamp"],
    )


def write_probe_cache(cache_path: Path, result: ProbeResult) -> None:
    try:
        existing = read_versioned_json(cache_path, _SCHEMA_VERSION, logger=logger)
        entries: dict[str, dict] = (existing or {}).get("entries", {})
        entries[result.cli_version] = {
            "policy_identity": result.policy_identity,
            "passed": result.passed,
            "failure_detail": result.failure_detail,
            "probe_timestamp": result.probe_timestamp,
        }
        write_versioned_json(cache_path, {"entries": entries}, _SCHEMA_VERSION)
    except OSError:
        logger.debug("probe_cache_write_failed", path=str(cache_path), exc_info=True)
