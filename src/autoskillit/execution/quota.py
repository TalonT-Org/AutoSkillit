"""Quota-aware check for long-running pipeline recipes.

IL-1 module: depends only on stdlib, httpx (FastMCP transitive dep), and core/logging.
Does NOT sleep. Returns metadata; the orchestrator sleeps via run_cmd.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    InfraExitCategory,
    SkillResult,
    acquire_flock_with_timeout,
    get_logger,
    read_versioned_json,
    write_versioned_json,
)
from autoskillit.quota_constraints import (
    OBSERVED_CONSTRAINT_SCHEMA_VERSION,
    QuotaConstraint,
    QuotaEvidenceSource,
    effective_quota_block,
    observed_constraint_path,
    quota_scope,
    safe_decode_observed_constraints,
)

logger = get_logger(__name__)

_DEFAULT_BASE_URL: str = "https://api.anthropic.com"

QUOTA_CACHE_SCHEMA_VERSION: int = 3

# Canonical Anthropic quota API window names, as returned by GET /api/oauth/usage.
# Update these when Anthropic adds or renames windows — the contract tests will
# catch any mismatch between these constants and the configured long_window_patterns.
KNOWN_QUOTA_WINDOW_NAMES: frozenset[str] = frozenset(
    {
        "five_hour",
        "one_hour",
        "one_day",
        "seven_day",
    }
)

# Subset of KNOWN_QUOTA_WINDOW_NAMES that represent long (multi-day) rate-limit windows.
# These names must be matched by at least one pattern in long_window_patterns.
# When Anthropic adds a new long window, add it here and update long_window_patterns defaults.
LONG_WINDOW_NAMES: frozenset[str] = frozenset(
    {
        "seven_day",
    }
)


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


def _is_long_window(name: str, long_patterns: list[str]) -> bool:
    """Return True if the window name matches any long-window pattern."""
    lowered = name.lower()
    return any(pat.lower() in lowered for pat in long_patterns)


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
    if _is_long_window(name, long_patterns):
        return long_threshold
    return short_threshold


def _compute_binding(
    windows: dict[str, QuotaWindowEntry],
    *,
    short_threshold: float,
    long_threshold: float,
    long_patterns: list[str],
    short_enabled: bool = True,
    long_enabled: bool = True,
) -> QuotaStatus:
    """Select the worst-case (binding) window using per-window thresholds.

    Each window is classified by name into short or long via long_patterns.
    Windows whose class is disabled are dropped before threshold evaluation.
    Among windows at or above their own threshold, returns the one with the
    latest resets_at. If none are exhausted, returns the window with highest
    utilization (for diagnostic display; should_block will be False).
    Returns QuotaStatus(0.0, None, ...) when windows is empty or all dropped.
    """
    if not windows:
        return QuotaStatus(0.0, None, effective_threshold=100.0)

    filtered = {
        name: w
        for name, w in windows.items()
        if (long_enabled if _is_long_window(name, long_patterns) else short_enabled)
    }
    if not filtered:
        return QuotaStatus(0.0, None, effective_threshold=100.0)

    def threshold_of(name: str) -> float:
        return _threshold_for_window(
            name,
            short_threshold=short_threshold,
            long_threshold=long_threshold,
            long_patterns=long_patterns,
        )

    exhausted = [(name, w) for name, w in filtered.items() if w.utilization >= threshold_of(name)]
    if exhausted:
        name, w = max(
            exhausted,
            key=lambda nw: nw[1].resets_at or datetime.min.replace(tzinfo=UTC),
        )
    else:
        name, w = max(filtered.items(), key=lambda nw: nw[1].utilization)

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
    raw = read_versioned_json(
        Path(cache_path).expanduser(),
        QUOTA_CACHE_SCHEMA_VERSION,
        logger=logger,
    )
    if raw is None:
        return None
    try:
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
    except (KeyError, ValueError, TypeError):
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
        logger.warning("quota cache write failed", path=cache_path, error=str(exc))


def record_observed_rate_limit(
    config: Any,
    *,
    scope: str,
    resets_at_epoch: int,
    limit_type: str,
    now_epoch: int,
) -> None:
    """Record terminal rate-limit evidence without touching the poll cache."""
    path = observed_constraint_path(config.cache_path)
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    # Atomic replacement prevents partial files; this lease serializes the
    # read-modify-write so concurrent observations are not lost.
    lock_acquired = False
    try:
        acquire_flock_with_timeout(
            fd,
            operation=fcntl.LOCK_EX,
            timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
            path=lock_path,
        )
        lock_acquired = True
        constraints = [
            constraint
            for constraint in safe_decode_observed_constraints(path)
            if constraint.blocked_until_epoch > now_epoch
        ]
        constraints.append(
            QuotaConstraint(
                source=QuotaEvidenceSource.OBSERVED_TERMINAL,
                scope=scope,
                blocked_until_epoch=resets_at_epoch,
                observed_at_epoch=now_epoch,
                limit_type=limit_type,
            )
        )
        write_versioned_json(
            path,
            {"constraints": [constraint.to_dict() for constraint in constraints]},
            schema_version=OBSERVED_CONSTRAINT_SCHEMA_VERSION,
        )
    finally:
        # Only release the flock when we actually acquired it. Calling LOCK_UN
        # on an unlocked fd can mask the upstream TimeoutError by blocking
        # indefinitely waiting for a non-existent lock to unlock.
        if lock_acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as exc:
                logger.warning(
                    "quota_observed_lock_release_failed",
                    path=str(lock_path),
                    error=str(exc),
                )
        os.close(fd)


def record_skill_result_rate_limit(
    skill_result: SkillResult,
    supports_quota_check: bool,
    config: Any | None,
    *,
    now_epoch: int | None = None,
) -> None:
    """Project structured terminal reset evidence into the observed store.

    When the boundary checks below fail (no config, quota checks unsupported by
    backend, infra exit not classified as RATE_LIMITED, or missing rate_limit
    fields) this is a meaningful decision — the PR's goal is "retain provider
    failure evidence", so silent drops undermine it. Each no-op path logs at
    ``debug`` with the suppressed fields so an operator investigating why a
    429 did not project into the observed store can find the reason without
    rerunning the session.
    """
    rate_limit = skill_result.api_failure.rate_limit
    if config is None:
        logger.debug(
            "quota_observed_evidence_skipped",
            skip_reason="no_quota_guard_config",
            exit_category=skill_result.infra.exit_category,
            supports_quota_check=supports_quota_check,
            resets_at_epoch=rate_limit.resets_at_epoch,
            limit_type=rate_limit.limit_type,
        )
        return
    if not supports_quota_check:
        logger.debug(
            "quota_observed_evidence_skipped",
            skip_reason="backend_does_not_support_quota_check",
            exit_category=skill_result.infra.exit_category,
            resets_at_epoch=rate_limit.resets_at_epoch,
            limit_type=rate_limit.limit_type,
        )
        return
    if skill_result.infra.exit_category != InfraExitCategory.RATE_LIMITED.value:
        logger.debug(
            "quota_observed_evidence_skipped",
            skip_reason=(f"exit_category={skill_result.infra.exit_category!r} (not RATE_LIMITED)"),
            supports_quota_check=supports_quota_check,
            resets_at_epoch=rate_limit.resets_at_epoch,
            limit_type=rate_limit.limit_type,
        )
        return
    if rate_limit.resets_at_epoch is None:
        logger.debug(
            "quota_observed_evidence_skipped",
            skip_reason="rate_limit_resets_at_epoch is None",
            exit_category=skill_result.infra.exit_category,
            supports_quota_check=supports_quota_check,
            limit_type=rate_limit.limit_type,
        )
        return
    if not rate_limit.limit_type:
        logger.debug(
            "quota_observed_evidence_skipped",
            skip_reason="rate_limit.limit_type is empty",
            exit_category=skill_result.infra.exit_category,
            supports_quota_check=supports_quota_check,
            resets_at_epoch=rate_limit.resets_at_epoch,
        )
        return
    try:
        record_observed_rate_limit(
            config,
            scope=quota_scope("anthropic", Path(config.credentials_path).expanduser()),
            resets_at_epoch=rate_limit.resets_at_epoch,
            limit_type=rate_limit.limit_type,
            now_epoch=now_epoch if now_epoch is not None else int(time.time()),
        )
    except Exception as exc:
        # Quota evidence is a side-channel; failure must never abort the headless
        # execution path that already classified this run as RATE_LIMITED.
        logger.warning(
            "quota_observed_evidence_persist_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )


def invalidate_cache(cache_path: str) -> None:
    """Remove the quota cache file so the next read triggers a live fetch.

    Tolerates missing files and logs a warning on permission errors.
    """
    try:
        Path(cache_path).expanduser().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("quota cache invalidation failed", path=cache_path, error=str(exc))


async def _fetch_quota(
    credentials_path: str,
    *,
    short_threshold: float,
    long_threshold: float,
    long_patterns: list[str],
    short_enabled: bool = True,
    long_enabled: bool = True,
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
    novel = {name for name in windows if name not in KNOWN_QUOTA_WINDOW_NAMES}
    if novel:
        logger.warning(
            "Anthropic quota API returned unknown quota window names. "
            "If this is a new rate-limit window, add it to KNOWN_QUOTA_WINDOW_NAMES in quota.py "
            "and update LONG_WINDOW_NAMES and long_window_patterns if it is a long window.",
            novel_windows=sorted(novel),
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
        short_enabled=short_enabled,
        long_enabled=long_enabled,
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
        short_enabled=config.short_window_enabled,
        long_enabled=config.long_window_enabled,
        base_url=base_url,
        _httpx_timeout=_httpx_timeout,
    )
    _write_cache(config.cache_path, fetch_result)


def _poll_constraint(status: QuotaStatus, *, scope: str, now_epoch: int) -> QuotaConstraint | None:
    if not status.should_block or status.resets_at is None:
        return None
    return QuotaConstraint(
        source=QuotaEvidenceSource.PROVIDER_POLL,
        scope=scope,
        blocked_until_epoch=int(status.resets_at.timestamp()),
        observed_at_epoch=now_epoch,
        limit_type=status.window_name,
    )


def _render_constraint(
    constraint: QuotaConstraint,
    *,
    status: QuotaStatus | None,
    buffer_seconds: int,
    now_epoch: int,
) -> dict[str, object]:
    sleep_seconds = max(0, constraint.blocked_until_epoch + buffer_seconds - now_epoch)
    return {
        "should_sleep": True,
        "sleep_seconds": sleep_seconds,
        "utilization": (
            status.utilization
            if status is not None and getattr(status, "utilization", None) is not None
            else 0.0
        ),
        "resets_at": datetime.fromtimestamp(constraint.blocked_until_epoch, tz=UTC).isoformat(),
        "window_name": constraint.limit_type
        or (status.window_name if status is not None else "unknown"),
        "effective_threshold": (status.effective_threshold if status is not None else 0.0),
        "block_source": constraint.source.value,
    }


async def check_and_sleep_if_needed(
    config: Any,
    *,
    provider: str = "anthropic",
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
        provider: Provider name. Non-anthropic providers bypass all quota I/O.

    Returns:
        {"should_sleep": bool, "sleep_seconds": int, "utilization": float,
         "resets_at": str | None, "window_name": str | None}
        On error: adds "error" key, sets should_sleep=False.
        On provider bypass: adds "provider_bypass": True key.
    """
    if not config.enabled:
        return {
            "should_sleep": False,
            "sleep_seconds": 0,
            "utilization": None,
            "resets_at": None,
            "window_name": None,
        }

    if provider.casefold() != "anthropic":
        return {
            "should_sleep": False,
            "sleep_seconds": 0,
            "utilization": None,
            "resets_at": None,
            "window_name": None,
            "provider_bypass": True,
        }

    fetch_kwargs = {
        "short_threshold": config.short_window_threshold,
        "long_threshold": config.long_window_threshold,
        "long_patterns": list(config.long_window_patterns),
        "short_enabled": config.short_window_enabled,
        "long_enabled": config.long_window_enabled,
        "base_url": base_url,
        "_httpx_timeout": _httpx_timeout,
    }

    now_epoch = int(time.time())
    account_scope = quota_scope(provider.casefold(), Path(config.credentials_path).expanduser())
    status: QuotaStatus | None = None
    refetched = False
    observations: list = []
    observed_winner: QuotaConstraint | None = None

    try:
        observations = safe_decode_observed_constraints(
            observed_constraint_path(config.cache_path)
        )
        observed_winner = effective_quota_block(
            observations, account_scope=account_scope, now_epoch=now_epoch
        )
        status = _read_cache(config.cache_path, config.cache_max_age)
        if status is None:
            if observed_winner is not None:
                return _render_constraint(
                    observed_winner,
                    status=None,
                    buffer_seconds=config.buffer_seconds,
                    now_epoch=now_epoch,
                )
            fetch_result = await _fetch_quota(config.credentials_path, **fetch_kwargs)
            _write_cache(config.cache_path, fetch_result)
            status = fetch_result.binding
            if status.should_block and status.resets_at is not None:
                refetched = True
                fetch_result = await _fetch_quota(config.credentials_path, **fetch_kwargs)
                _write_cache(config.cache_path, fetch_result)
                status = fetch_result.binding
        elif status.should_block and status.resets_at is not None:
            # Preserve the existing accuracy re-fetch for a cached blocker.
            refetched = True
            fetch_result = await _fetch_quota(config.credentials_path, **fetch_kwargs)
            _write_cache(config.cache_path, fetch_result)
            status = fetch_result.binding

        constraints = list(observations)
        poll_constraint = _poll_constraint(status, scope=account_scope, now_epoch=now_epoch)
        if poll_constraint is not None:
            constraints.append(poll_constraint)
        winner = effective_quota_block(
            constraints, account_scope=account_scope, now_epoch=now_epoch
        )
        if winner is not None:
            return _render_constraint(
                winner,
                status=status,
                buffer_seconds=config.buffer_seconds,
                now_epoch=now_epoch,
            )

        if status.should_block and status.resets_at is None:
            fallback_seconds = max(config.buffer_seconds, 60)
            logger.warning(
                (
                    "quota above threshold but resets_at is None after re-fetch"
                    " — blocking with fallback"
                    if refetched
                    else "quota above threshold but resets_at is None — blocking with fallback"
                ),
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

        return {
            "should_sleep": False,
            "sleep_seconds": 0,
            "utilization": status.utilization,
            "resets_at": status.resets_at.isoformat() if status.resets_at else None,
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
            logger.warning(
                "quota check failed — continuing without sleep",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
        else:
            logger.error(
                "quota check failed (unexpected error) — continuing without sleep",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
        if observed_winner is not None:
            return _render_constraint(
                observed_winner,
                status=status,
                buffer_seconds=config.buffer_seconds,
                now_epoch=now_epoch,
            )
        return {
            "should_sleep": False,
            "sleep_seconds": 0,
            "utilization": None,
            "resets_at": None,
            "window_name": None,
            "error": str(exc),
        }
