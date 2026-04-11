"""Quota-aware check for long-running pipeline recipes.

L1 module: depends only on stdlib, httpx (FastMCP transitive dep), and core/logging.
Does NOT sleep. Returns metadata; the orchestrator sleeps via run_cmd.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from autoskillit.core import get_logger, write_versioned_json

_log = get_logger(__name__)

_DEFAULT_BASE_URL: str = "https://api.anthropic.com"

QUOTA_CACHE_SCHEMA_VERSION: int = 3
_SCHEMA_DRIFT_LOGGED: set[str] = set()


def _reset_schema_drift_logged_for_tests() -> None:
    """Test-only helper: clear the once-per-process drift-log set."""
    _SCHEMA_DRIFT_LOGGED.clear()


@dataclass
class QuotaStatus:
    utilization: float  # percentage 0–100
    resets_at: datetime | None  # UTC-aware; None when utilization is 0
    window_name: str = "unknown"  # which window this came from (diagnostic)
    should_block: bool = False
    effective_threshold: float = 0.0

    def __post_init__(self) -> None:
        if self.utilization is None:
            raise TypeError("QuotaStatus.utilization must not be None")
        self.utilization = float(self.utilization)


@dataclass
class QuotaWindowEntry:
    """A single rate-limit window from the API response."""

    utilization: float
    resets_at: datetime | None

    def __post_init__(self) -> None:
        if self.utilization is None:
            raise TypeError("QuotaWindowEntry.utilization must not be None")
        self.utilization = float(self.utilization)


@dataclass
class QuotaFetchResult:
    """All windows from one API call, with the binding (worst-case) identified."""

    windows: dict[str, QuotaWindowEntry] = field(default_factory=dict)
    binding: QuotaStatus = field(default_factory=lambda: QuotaStatus(0.0, None))


def _parse_resets_at(resets_at_str: str | None) -> datetime | None:
    """Parse a resets_at string from API or cache, handling Z-suffix and +00:00 variants."""
    if not resets_at_str:
        return None
    return datetime.fromisoformat(resets_at_str.replace("Z", "+00:00"))


def _threshold_for_window(
    name: str,
    *,
    short_threshold: float,
    long_threshold: float,
    long_patterns: list[str],
) -> float:
    """Return the threshold to apply to a window of the given name.

    Long-window classification is substring match (case-insensitive) against
    long_patterns. Unknown windows fall through to short_threshold.
    """
    lowered = name.lower()
    for pat in long_patterns:
        if pat.lower() in lowered:
            return long_threshold
    return short_threshold


def _compute_binding(
    windows: dict[str, QuotaWindowEntry],
    *,
    short_threshold: float,
    long_threshold: float,
    long_patterns: list[str],
) -> QuotaStatus:
    """Select the worst-case (binding) window using per-window thresholds.

    Each window is classified by name into short or long via long_patterns.
    Among windows at or above their own threshold, returns the one with the
    latest resets_at. If none are exhausted, returns the window with highest
    utilization (for diagnostic display; should_block will be False).
    Returns QuotaStatus(0.0, None, ...) when windows is empty.
    """
    if not windows:
        return QuotaStatus(0.0, None, effective_threshold=100.0)

    def threshold_of(name: str) -> float:
        return _threshold_for_window(
            name,
            short_threshold=short_threshold,
            long_threshold=long_threshold,
            long_patterns=long_patterns,
        )

    exhausted = [(name, w) for name, w in windows.items() if w.utilization >= threshold_of(name)]
    if exhausted:
        name, w = max(
            exhausted,
            key=lambda nw: nw[1].resets_at or datetime.min.replace(tzinfo=UTC),
        )
    else:
        name, w = max(windows.items(), key=lambda nw: nw[1].utilization)

    effective = threshold_of(name)
    return QuotaStatus(
        utilization=w.utilization,
        resets_at=w.resets_at,
        window_name=name,
        should_block=w.utilization >= effective,
        effective_threshold=effective,
    )


def _read_credentials(credentials_path: str) -> str:
    """Read Bearer token from ~/.claude/.credentials.json.

    Raises PermissionError if the token is expired.
    """
    data = json.loads(Path(credentials_path).expanduser().read_text())
    creds = data["claudeAiOauth"]
    expires_ms = creds.get("expiresAt", 0)
    if time.time() * 1000 > expires_ms:
        raise PermissionError("OAuth access token is expired — re-run 'claude login'")
    return creds["accessToken"]


def _read_cache(cache_path: str, max_age: int) -> QuotaStatus | None:
    """Return a fresh QuotaStatus from local cache, or None if stale/missing/old-format."""
    try:
        raw = json.loads(Path(cache_path).expanduser().read_text())
        if not isinstance(raw, dict):
            return None
        if raw.get("schema_version") != QUOTA_CACHE_SCHEMA_VERSION:
            cache_key = str(Path(cache_path).expanduser())
            if cache_key not in _SCHEMA_DRIFT_LOGGED:
                _SCHEMA_DRIFT_LOGGED.add(cache_key)
                _log.warning(
                    "quota_cache_schema_drift",
                    cache_path=cache_key,
                    observed=raw.get("schema_version"),
                )
            return None
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        age = (datetime.now(UTC) - fetched_at).total_seconds()
        if age > max_age:
            return None
        if "binding" not in raw:
            return None
        b = raw["binding"]
        return QuotaStatus(
            utilization=float(b["utilization"]),
            resets_at=_parse_resets_at(b.get("resets_at")),
            window_name=str(b.get("window_name", "unknown")),
            should_block=bool(b.get("should_block", False)),
            effective_threshold=float(b.get("effective_threshold", 0.0)),
        )
    except (FileNotFoundError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cache(cache_path: str, result: QuotaFetchResult) -> None:
    """Write full-snapshot quota data to cache file. Silently logs on failure."""
    try:
        payload = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "windows": {
                name: {
                    "utilization": w.utilization,
                    "resets_at": w.resets_at.isoformat() if w.resets_at else None,
                }
                for name, w in result.windows.items()
            },
            "binding": {
                "window_name": result.binding.window_name,
                "utilization": result.binding.utilization,
                "resets_at": (
                    result.binding.resets_at.isoformat() if result.binding.resets_at else None
                ),
                "should_block": result.binding.should_block,
                "effective_threshold": result.binding.effective_threshold,
            },
        }
        path = Path(cache_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_versioned_json(path, payload, schema_version=QUOTA_CACHE_SCHEMA_VERSION)
    except OSError as exc:
        _log.warning("quota cache write failed", path=cache_path, error=str(exc))


async def _fetch_quota(
    credentials_path: str,
    *,
    short_threshold: float,
    long_threshold: float,
    long_patterns: list[str],
    base_url: str = _DEFAULT_BASE_URL,
    _httpx_timeout: float = 10,
) -> QuotaFetchResult:
    """Fetch all rate-limit windows from Anthropic quota API and identify the binding window."""
    token = _read_credentials(credentials_path)
    async with httpx.AsyncClient(timeout=_httpx_timeout) as client:
        resp = await client.get(
            f"{base_url}/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )
    resp.raise_for_status()
    data = resp.json()
    windows: dict[str, QuotaWindowEntry] = {}
    for name, w in data.items():
        if isinstance(w, dict) and "utilization" in w:
            raw_util = w["utilization"]
            if raw_util is None:
                continue
            windows[name] = QuotaWindowEntry(
                utilization=float(raw_util),
                resets_at=_parse_resets_at(w.get("resets_at")),
            )
    if not windows:
        return QuotaFetchResult(
            windows={}, binding=QuotaStatus(0.0, None, effective_threshold=100.0)
        )
    binding = _compute_binding(
        windows,
        short_threshold=short_threshold,
        long_threshold=long_threshold,
        long_patterns=long_patterns,
    )
    return QuotaFetchResult(windows=windows, binding=binding)


async def _refresh_quota_cache(
    config: Any,
    *,
    base_url: str = _DEFAULT_BASE_URL,
    _httpx_timeout: float = 10.0,
) -> None:
    """Fetch fresh quota status and write to cache unconditionally.

    Unlike check_and_sleep_if_needed, this function does NOT read the existing
    cache first. It always performs a live API call. Intended for use by the
    periodic background refresh loop, where proactive write-before-expiry is
    the goal, not avoiding redundant calls.

    Exceptions from _fetch_quota propagate to the caller for supervision.
    """
    fetch_result = await _fetch_quota(
        config.credentials_path,
        short_threshold=config.short_window_threshold,
        long_threshold=config.long_window_threshold,
        long_patterns=list(config.long_window_patterns),
        base_url=base_url,
        _httpx_timeout=_httpx_timeout,
    )
    _write_cache(config.cache_path, fetch_result)


async def check_and_sleep_if_needed(
    config: Any,
    *,
    base_url: str = _DEFAULT_BASE_URL,
    _httpx_timeout: float = 10,
) -> dict:
    """Check quota utilization. Returns metadata indicating whether a sleep is needed.

    Does NOT sleep. The caller is responsible for sleeping (e.g. via run_cmd).

    Cache is treated as authoritative when fresh (within config.cache_max_age seconds).
    A fresh cache hit skips the live Anthropic API call entirely — intentional, since quota
    status changes slowly and avoiding unnecessary API calls is preferable to marginal freshness.
    Live fetch only occurs on cache miss, expiry, or when utilization exceeds the threshold
    (where accurate resets_at is needed for sleep duration).

    Args:
        config: QuotaGuardConfig instance.

    Returns:
        {"should_sleep": bool, "sleep_seconds": int, "utilization": float | None,
         "resets_at": str | None, "window_name": str | None}
        On error: adds "error" key, sets should_sleep=False.
    """
    if not config.enabled:
        return {
            "should_sleep": False,
            "sleep_seconds": 0,
            "utilization": None,
            "resets_at": None,
            "window_name": None,
        }

    fetch_kwargs = {
        "short_threshold": config.short_window_threshold,
        "long_threshold": config.long_window_threshold,
        "long_patterns": list(config.long_window_patterns),
        "base_url": base_url,
        "_httpx_timeout": _httpx_timeout,
    }

    try:
        status = _read_cache(config.cache_path, config.cache_max_age)
        if status is None:
            fetch_result = await _fetch_quota(config.credentials_path, **fetch_kwargs)
            _write_cache(config.cache_path, fetch_result)
            status = fetch_result.binding

        if not status.should_block:
            return {
                "should_sleep": False,
                "sleep_seconds": 0,
                "utilization": status.utilization,
                "resets_at": status.resets_at.isoformat() if status.resets_at else None,
                "window_name": status.window_name,
                "effective_threshold": status.effective_threshold,
            }

        if status.resets_at is None:
            fallback_seconds = max(config.buffer_seconds, 60)
            _log.warning(
                "quota above threshold but resets_at is None — blocking with fallback",
                utilization=status.utilization,
                fallback_sleep_seconds=fallback_seconds,
            )
            return {
                "should_sleep": True,
                "sleep_seconds": fallback_seconds,
                "utilization": status.utilization,
                "resets_at": None,
                "window_name": status.window_name,
                "effective_threshold": status.effective_threshold,
                "reason": "unknown_reset",
            }

        # Re-fetch for accurate resets_at before returning sleep metadata
        fetch_result = await _fetch_quota(config.credentials_path, **fetch_kwargs)
        _write_cache(config.cache_path, fetch_result)
        status = fetch_result.binding

        if status.resets_at is None:
            fallback_seconds = max(config.buffer_seconds, 60)
            _log.warning(
                "quota above threshold but resets_at is None after re-fetch"
                " — blocking with fallback",
                utilization=status.utilization,
                fallback_sleep_seconds=fallback_seconds,
            )
            return {
                "should_sleep": True,
                "sleep_seconds": fallback_seconds,
                "utilization": status.utilization,
                "resets_at": None,
                "window_name": status.window_name,
                "effective_threshold": status.effective_threshold,
                "reason": "unknown_reset",
            }

        now = datetime.now(UTC)
        wake_at = status.resets_at + timedelta(seconds=config.buffer_seconds)
        sleep_secs = max(0, int((wake_at - now).total_seconds()))
        _log.info(
            "quota threshold exceeded — caller should sleep",
            utilization=status.utilization,
            effective_threshold=status.effective_threshold,
            window_name=status.window_name,
            sleep_seconds=sleep_secs,
            resets_at=status.resets_at.isoformat(),
        )
        return {
            "should_sleep": True,
            "sleep_seconds": sleep_secs,
            "utilization": status.utilization,
            "resets_at": status.resets_at.isoformat(),
            "window_name": status.window_name,
            "effective_threshold": status.effective_threshold,
        }

    except Exception as exc:
        # Fail-open subsystem-boundary contract: never raise on quota errors.
        # Split severity so operational failures stay at WARNING while programming
        # bugs (AttributeError, NameError, ImportError, ...) surface at ERROR in
        # dashboards instead of being masked as routine transient errors.
        _operational_types = (
            TimeoutError,
            OSError,
            KeyError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            httpx.HTTPError,
        )
        if isinstance(exc, _operational_types):
            _log.warning(
                "quota check failed — continuing without sleep",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
        else:
            _log.error(
                "quota check failed (unexpected error) — continuing without sleep",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
        return {
            "should_sleep": False,
            "sleep_seconds": 0,
            "utilization": None,
            "resets_at": None,
            "window_name": None,
            "error": str(exc),
        }
