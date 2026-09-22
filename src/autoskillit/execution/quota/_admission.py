"""Fail-closed quota admission for finalized execution launches.

Admission never sleeps for quota capacity.  It either returns a rejection that
the launch caller can surface immediately, or an allowed decision.  A launch
near an Anthropic OAuth threshold retains the exclusive credential lease until
the caller closes it after the attempted launch.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import anyio

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    RateLimitWindow,
    get_logger,
)
from autoskillit.execution.quota._quota_gate import (
    QuotaFetchResult,
    QuotaStatus,
    _fetch_quota,
    _is_long_window,
    _threshold_for_window,
)
from autoskillit.quota_constraints import (
    QuotaConstraint,
    effective_quota_block,
    observed_constraint_path,
    quota_scope,
    safe_decode_observed_constraints,
)

logger = get_logger(__name__)

_NEAR_THRESHOLD_MARGIN = 5.0


class QuotaAdmissionConfigLike(Protocol):
    """Configuration fields admission needs without importing the config layer."""

    enabled: bool
    short_window_enabled: bool
    long_window_enabled: bool
    short_window_threshold: float
    long_window_threshold: float
    long_window_patterns: list[str]
    credentials_path: str
    cache_path: str


@dataclass(frozen=True, slots=True)
class QuotaAdmission:
    """One pre-spawn quota decision and any lease retained by an admitted launch."""

    admitted: bool
    reason: str
    lease: ArtifactLease | None = None
    rate_limit: RateLimitWindow = field(default_factory=RateLimitWindow)


def oauth_admission_lock_path(diagnostic_log_root: str | Path) -> Path:
    """Return the shared OAuth credential-read lock beneath the diagnostics root."""
    if not isinstance(diagnostic_log_root, (str, Path)):
        raise TypeError("diagnostic_log_root must be a str or Path")
    return Path(diagnostic_log_root) / "quota-admission" / "anthropic-oauth.lock"


def _rejection_reason(
    *, deadline_monotonic: float | None, cancelled: Callable[[], bool] | None
) -> str | None:
    if cancelled is not None and cancelled():
        return "admission_cancelled"
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return "deadline_exceeded"
    return None


def _oauth_scope_matches(config: QuotaAdmissionConfigLike, credential_scope: str) -> bool:
    try:
        return (
            quota_scope("anthropic", Path(config.credentials_path).expanduser())
            == credential_scope
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _observed_block(
    config: QuotaAdmissionConfigLike, *, binding_scope: str | None
) -> QuotaConstraint | None:
    if not binding_scope:
        return None
    return effective_quota_block(
        safe_decode_observed_constraints(observed_constraint_path(config.cache_path)),
        account_scope=binding_scope,
        now_epoch=int(time.time()),
    )


def _constraint_rate_limit(constraint: QuotaConstraint) -> RateLimitWindow:
    return RateLimitWindow(
        status="rejected",
        limit_type=constraint.limit_type,
        resets_at_epoch=constraint.blocked_until_epoch,
    )


def _status_rate_limit(status: QuotaStatus) -> RateLimitWindow:
    return RateLimitWindow(
        status="rejected",
        limit_type=status.window_name,
        resets_at_epoch=int(status.resets_at.timestamp())
        if status.resets_at is not None
        else None,
    )


def _near_threshold(fetch: QuotaFetchResult, config: QuotaAdmissionConfigLike) -> bool:
    for name, window in fetch.windows.items():
        is_long = _is_long_window(name, config.long_window_patterns)
        if not (config.long_window_enabled if is_long else config.short_window_enabled):
            continue
        threshold = _threshold_for_window(
            name,
            short_threshold=config.short_window_threshold,
            long_threshold=config.long_window_threshold,
            long_patterns=config.long_window_patterns,
        )
        if window.utilization >= threshold - _NEAR_THRESHOLD_MARGIN:
            return True
    return False


async def _fetch_authoritative(
    config: QuotaAdmissionConfigLike,
    *,
    deadline_monotonic: float | None,
) -> QuotaFetchResult:
    fetch = _fetch_quota(
        config.credentials_path,
        short_threshold=config.short_window_threshold,
        long_threshold=config.long_window_threshold,
        long_patterns=list(config.long_window_patterns),
        short_enabled=config.short_window_enabled,
        long_enabled=config.long_window_enabled,
    )
    if deadline_monotonic is None:
        return await fetch
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    with anyio.fail_after(remaining):
        return await fetch


async def admit_quota(
    *,
    config: QuotaAdmissionConfigLike,
    credential_scope: str | None,
    diagnostic_log_root: str | Path,
    deadline_monotonic: float | None,
    provider: str = "anthropic",
    binding_scope: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    nested_worker: bool = False,
) -> QuotaAdmission:
    """Admit one finalized launch without waiting for quota availability.

    Observed terminal limits are checked for every credential-bound launch,
    including external providers and Codex.  Only an Anthropic OAuth launch
    needs an authoritative usage poll and the shared exclusive lease.
    """
    rejection = _rejection_reason(
        deadline_monotonic=deadline_monotonic,
        cancelled=cancelled,
    )
    if rejection is not None:
        return QuotaAdmission(False, rejection)
    if not config.enabled:
        return QuotaAdmission(True, "quota_guard_disabled")

    scope = binding_scope or credential_scope
    observed = _observed_block(config, binding_scope=scope)
    if observed is not None:
        return QuotaAdmission(
            False,
            "observed_quota_blocked",
            rate_limit=_constraint_rate_limit(observed),
        )

    if provider.casefold() != "anthropic":
        return QuotaAdmission(True, "provider_bypass")
    if credential_scope is None:
        return QuotaAdmission(True, "quota_poll_bypassed")
    if not credential_scope.startswith("anthropic-oauth:"):
        return QuotaAdmission(True, "quota_poll_bypassed")

    if not _oauth_scope_matches(config, credential_scope):
        return QuotaAdmission(False, "quota_authority_unknown")

    lease: ArtifactLease | None = None
    retain_lease = False
    try:
        rejection = _rejection_reason(
            deadline_monotonic=deadline_monotonic,
            cancelled=cancelled,
        )
        if rejection is not None:
            return QuotaAdmission(False, rejection)
        lease = ArtifactLease.acquire_exclusive(
            oauth_admission_lock_path(diagnostic_log_root),
            timeout=2.0,
        )
        rejection = _rejection_reason(
            deadline_monotonic=deadline_monotonic,
            cancelled=cancelled,
        )
        if rejection is not None:
            return QuotaAdmission(False, rejection)

        fetched = await _fetch_authoritative(
            config,
            deadline_monotonic=deadline_monotonic,
        )
        if not _oauth_scope_matches(config, credential_scope):
            return QuotaAdmission(False, "quota_authority_unknown")

        rejection = _rejection_reason(
            deadline_monotonic=deadline_monotonic,
            cancelled=cancelled,
        )
        if rejection is not None:
            return QuotaAdmission(False, rejection)
        observed = _observed_block(config, binding_scope=scope)
        if observed is not None:
            return QuotaAdmission(
                False,
                "observed_quota_blocked",
                rate_limit=_constraint_rate_limit(observed),
            )

        status = fetched.binding
        if status.should_block:
            return QuotaAdmission(
                False,
                "quota_exhausted",
                rate_limit=_status_rate_limit(status),
            )
        if _near_threshold(fetched, config):
            fetched = await _fetch_authoritative(
                config,
                deadline_monotonic=deadline_monotonic,
            )
            if not _oauth_scope_matches(config, credential_scope):
                return QuotaAdmission(False, "quota_authority_unknown")
            status = fetched.binding
            if status.should_block:
                return QuotaAdmission(
                    False,
                    "quota_exhausted",
                    rate_limit=_status_rate_limit(status),
                )
            rejection = _rejection_reason(
                deadline_monotonic=deadline_monotonic,
                cancelled=cancelled,
            )
            if rejection is not None:
                return QuotaAdmission(False, rejection)
            if _near_threshold(fetched, config):
                if nested_worker:
                    return QuotaAdmission(
                        False,
                        "near_quota_threshold",
                        rate_limit=_status_rate_limit(status),
                    )
                retain_lease = True
                return QuotaAdmission(True, "quota_admitted", lease=lease)

        return QuotaAdmission(True, "quota_admitted")
    except ArtifactLeaseContention:
        return QuotaAdmission(False, "quota_admission_contended")
    except TimeoutError:
        rejection = _rejection_reason(
            deadline_monotonic=deadline_monotonic,
            cancelled=cancelled,
        )
        return QuotaAdmission(False, rejection or "quota_authority_unavailable")
    except Exception as exc:
        logger.warning(
            "quota_admission_authority_unavailable",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return QuotaAdmission(False, "quota_authority_unavailable")
    finally:
        if lease is not None and not retain_lease:
            lease.close_preserving()
