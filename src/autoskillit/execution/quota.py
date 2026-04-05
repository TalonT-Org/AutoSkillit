"""Quota-aware check for long-running pipeline recipes.

L1 module: depends only on stdlib, httpx (FastMCP transitive dep), and core/logging.
Does NOT sleep. Returns metadata; the orchestrator sleeps via run_cmd.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from autoskillit.core import atomic_write, get_logger

_log = get_logger(__name__)

_DEFAULT_BASE_URL: str = "https://api.anthropic.com"


@dataclass
class QuotaStatus:
    utilization: float  # percentage 0–100
    resets_at: datetime | None  # UTC-aware; None when utilization is 0


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
    """Return a fresh QuotaStatus from local cache, or None if stale/missing."""
    try:
        raw = json.loads(Path(cache_path).expanduser().read_text())
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        age = (datetime.now(UTC) - fetched_at).total_seconds()
        if age > max_age:
            return None
        fh = raw["five_hour"]
        resets_at_str = fh.get("resets_at")
        resets_at = datetime.fromisoformat(resets_at_str) if resets_at_str else None
        return QuotaStatus(
            utilization=float(fh["utilization"]),
            resets_at=resets_at,
        )
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _write_cache(cache_path: str, status: QuotaStatus) -> None:
    """Write quota status to cache file. Silently logs on failure."""
    try:
        payload = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "five_hour": {
                "utilization": status.utilization,
                "resets_at": status.resets_at.isoformat() if status.resets_at else None,
            },
        }
        path = Path(cache_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(payload))
    except OSError as exc:
        _log.warning("quota cache write failed", path=cache_path, error=str(exc))


async def _fetch_quota(
    credentials_path: str,
    *,
    base_url: str = _DEFAULT_BASE_URL,
    _httpx_timeout: float = 10,
) -> QuotaStatus:
    """Fetch 5-hour utilization from Anthropic quota API."""
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
    fh = data["five_hour"]
    resets_at_str = fh.get("resets_at")
    resets_at = (
        datetime.fromisoformat(resets_at_str.replace("Z", "+00:00")) if resets_at_str else None
    )
    return QuotaStatus(
        utilization=float(fh["utilization"]),
        resets_at=resets_at,
    )


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
         "resets_at": str | None}
        On error: adds "error" key, sets should_sleep=False.
    """
    if not config.enabled:
        return {"should_sleep": False, "sleep_seconds": 0, "utilization": None, "resets_at": None}

    try:
        status = _read_cache(config.cache_path, config.cache_max_age)
        if status is None:
            status = await _fetch_quota(
                config.credentials_path, base_url=base_url, _httpx_timeout=_httpx_timeout
            )
            _write_cache(config.cache_path, status)

        if status.utilization < config.threshold:
            return {
                "should_sleep": False,
                "sleep_seconds": 0,
                "utilization": status.utilization,
                "resets_at": status.resets_at.isoformat() if status.resets_at else None,
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
                "reason": "unknown_reset",
            }

        # Re-fetch for accurate resets_at before returning sleep metadata
        status = await _fetch_quota(
            config.credentials_path, base_url=base_url, _httpx_timeout=_httpx_timeout
        )
        _write_cache(config.cache_path, status)

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
                "reason": "unknown_reset",
            }

        now = datetime.now(UTC)
        wake_at = status.resets_at + timedelta(seconds=config.buffer_seconds)
        sleep_secs = max(0, int((wake_at - now).total_seconds()))
        _log.info(
            "quota threshold exceeded — caller should sleep",
            utilization=status.utilization,
            threshold=config.threshold,
            sleep_seconds=sleep_secs,
            resets_at=status.resets_at.isoformat(),
        )
        return {
            "should_sleep": True,
            "sleep_seconds": sleep_secs,
            "utilization": status.utilization,
            "resets_at": status.resets_at.isoformat(),
        }

    except (
        TimeoutError,
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        httpx.HTTPError,
    ) as exc:
        _log.warning("quota check failed — continuing without sleep", error=str(exc))
        return {
            "should_sleep": False,
            "sleep_seconds": 0,
            "utilization": None,
            "resets_at": None,
            "error": str(exc),
        }
