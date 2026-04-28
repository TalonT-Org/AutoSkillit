"""GitHub fetch cache and version-fetching helpers extracted from _update_checks.py."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION, atomic_write, get_logger

logger = get_logger(__name__)

# Disposable on-disk cache for GitHub API GETs.  This is intentionally a plain
# JSON dict (not ``write_versioned_json``) — it is a transient performance
# cache, not a persisted schema artifact, so a stray pre-existing file at the
# same path is safely ignored on a JSON parse failure.
_FETCH_CACHE_FILE = "github_fetch_cache.json"
_DEFAULT_FETCH_TTL_SECONDS = 30 * 60

# connect=2s buffers for cold DNS (httpx's connect timeout does NOT cover the
# OS resolver itself); read=1s is tight because 304 responses are tiny.  The
# 30-min disk TTL above is the **only reliable quota defense** — GitHub's
# x-ratelimit-used counter has been observed to increment on 304 responses
# despite their docs claiming otherwise, so ETag/If-None-Match is a
# bandwidth/latency optimization, not a rate-limit optimization.
_HTTP_TIMEOUT = httpx.Timeout(connect=2.0, read=1.0, write=5.0, pool=1.0)
_GITHUB_API_VERSION = "2022-11-28"

_GITHUB_RELEASES_URL = "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest"
_GITHUB_INTEGRATION_PYPROJECT_URL = (
    "https://api.github.com/repos/TalonT-Org/AutoSkillit/contents/pyproject.toml?ref=integration"
)


def _fetch_cache_path(home: Path) -> Path:
    return home / ".autoskillit" / _FETCH_CACHE_FILE


def _read_fetch_cache(home: Path) -> dict[str, Any]:
    """Read the GitHub fetch cache; tolerate any read/parse failure."""
    try:
        data = json.loads(_fetch_cache_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("Failed to read fetch cache", exc_info=True)
        return {}


def _write_fetch_cache(home: Path, data: dict[str, Any]) -> None:
    """Write the GitHub fetch cache atomically."""
    target = _fetch_cache_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write(target, json.dumps(data))
    except Exception:
        logger.debug("Failed to write fetch cache", exc_info=False)


def invalidate_fetch_cache(home: Path) -> None:
    """Delete the GitHub fetch cache. Call after install/update."""
    try:
        _fetch_cache_path(home).unlink(missing_ok=True)
    except OSError:
        logger.debug("Failed to invalidate fetch cache", exc_info=True)


def _scrub_auth(text: str) -> str:
    """Remove any GITHUB_TOKEN value or ``Bearer …`` token from a string."""
    if not text:
        return text
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        text = text.replace(token, "***")
    import re

    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer ***", text)
    return text


def _resolve_fetch_ttl() -> int:
    raw = os.environ.get("AUTOSKILLIT_FETCH_CACHE_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_FETCH_TTL_SECONDS
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_FETCH_TTL_SECONDS


def _fetch_with_cache(url: str, *, home: Path, ttl: int | None = None) -> dict[str, Any] | None:
    """Fetch ``url`` via httpx with a disk-backed cache and conditional GETs.

    Returns the parsed JSON dict on success (200 or 304), or ``None`` on any
    error.  ``Authorization`` headers are scrubbed from log output to prevent
    token leakage via DEBUG-level error reporting.

    The 30-minute disk TTL is the only reliable quota defense — see
    ``_DEFAULT_FETCH_TTL_SECONDS`` for the rationale.
    """
    effective_ttl = ttl if ttl is not None else _resolve_fetch_ttl()
    cache = _read_fetch_cache(home)
    entry = cache.get(url) if isinstance(cache.get(url), dict) else None
    now = time.time()
    if entry is not None:
        cached_at = entry.get("cached_at")
        body = entry.get("body")
        if isinstance(cached_at, (int, float)) and isinstance(body, dict):
            if now - cached_at < effective_ttl:
                if entry.get("installed_version") == AUTOSKILLIT_INSTALLED_VERSION:
                    return body

    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "User-Agent": f"autoskillit/{AUTOSKILLIT_INSTALLED_VERSION}",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if entry is not None and isinstance(entry.get("etag"), str):
        headers["If-None-Match"] = entry["etag"]

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            response = client.get(url, headers=headers)
    except Exception as exc:
        logger.debug("fetch failed for %s: %s", url, _scrub_auth(str(exc)))
        return None

    try:
        if response.status_code == 304 and entry is not None:
            body = entry.get("body")
            if isinstance(body, dict):
                cache[url] = {
                    "body": body,
                    "etag": entry.get("etag"),
                    "cached_at": now,
                    "installed_version": AUTOSKILLIT_INSTALLED_VERSION,
                }
                _write_fetch_cache(home, cache)
                return body
            return None
        if response.status_code == 200:
            body = response.json()
            if not isinstance(body, dict):
                return None
            etag = response.headers.get("ETag")
            cache[url] = {
                "body": body,
                "etag": etag,
                "cached_at": now,
                "installed_version": AUTOSKILLIT_INSTALLED_VERSION,
            }
            _write_fetch_cache(home, cache)
            return body
        logger.debug("fetch %s returned status %d", url, response.status_code)
        return None
    except Exception as exc:
        logger.debug("fetch parse failed for %s: %s", url, _scrub_auth(str(exc)))
        return None


def _fetch_latest_version(target: str, home: Path) -> str | None:
    """Fetch the latest available version for the given target branch.

    ``target`` is either ``"releases/latest"`` (for stable/main installs) or
    ``"integration"`` (for integration installs).

    Returns ``None`` on any network error or timeout.
    """
    try:
        if target == "integration":
            data = _fetch_with_cache(_GITHUB_INTEGRATION_PYPROJECT_URL, home=home)
            if data is None:
                return None
            import base64

            raw_content = data.get("content")
            if not isinstance(raw_content, str):
                return None
            content = base64.b64decode(raw_content).decode("utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.split("=", 1)[0].strip() == "version":
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
            return None
        data = _fetch_with_cache(_GITHUB_RELEASES_URL, home=home)
        if data is None:
            return None
        tag = data.get("tag_name", "")
        return tag.lstrip("v") if isinstance(tag, str) and tag else None
    except Exception as exc:
        logger.debug("Failed to fetch latest version from GitHub: %s", _scrub_auth(str(exc)))
        return None
