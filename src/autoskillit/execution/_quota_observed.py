"""Observed-rate-limit evidence persistence, split out of quota.py (REQ-CNST-010).

Projects a session's structured terminal rate-limit evidence (from
``SkillResult.api_failure.rate_limit``) into the durable observed-constraints
store, independent of the poll-based quota gate in ``quota.py``. Nothing here
reads the poll cache or computes a block decision — that fold lives in
``quota_constraints.effective_quota_block``, consulted by ``quota.py``'s
``check_and_sleep_if_needed``.

IL-1 module: depends only on stdlib, httpx (for the shared operational-vs-bug
exception split; not used for I/O here), and core/logging.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Protocol

import httpx

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    InfraExitCategory,
    RateLimitWindow,
    SkillResult,
    acquire_flock_with_timeout,
    get_logger,
    write_versioned_json,
)
from autoskillit.quota_constraints import (
    OBSERVED_CONSTRAINT_SCHEMA_VERSION,
    QuotaConstraint,
    QuotaEvidenceSource,
    observed_constraint_path,
    quota_scope,
    safe_decode_observed_constraints,
)

logger = get_logger(__name__)

# Shared severity-split for this module's and quota.py's fail-open exception
# boundaries: an operational failure (I/O, lock contention, malformed JSON,
# HTTP) stays at WARNING, while anything outside this set is a programming bug
# that should surface at ERROR instead of being masked as routine. Used by
# both record_skill_result_rate_limit (below) and quota.py's
# check_and_sleep_if_needed, which imports this tuple from here — each catches
# Exception broadly (never raising, per the fail-open contract) but splits the
# log severity by isinstance against this tuple.
_OPERATIONAL_EXCEPTION_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    OSError,
    KeyError,
    ValueError,
    TypeError,
    json.JSONDecodeError,
    httpx.HTTPError,
)


class QuotaPersistenceConfigLike(Protocol):
    """Structural contract for the persistence-path recorder functions below.

    ``execution/`` (IL-1) may not import ``autoskillit.config`` (a higher
    layer), so ``record_observed_rate_limit``/``record_skill_result_rate_limit``
    cannot type their ``config`` parameter as the concrete ``QuotaGuardConfig``
    dataclass. A local Protocol restores real attribute-access type coverage
    for the two fields these functions actually touch, mirroring the
    ``EvidenceReaderInvocationLike`` pattern in ``execution/evidence_reader.py``.
    """

    credentials_path: str
    cache_path: str


def record_observed_rate_limit(
    config: QuotaPersistenceConfigLike,
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


def _skill_result_rate_limit_skip_reason(
    *,
    skill_result: SkillResult,
    supports_quota_check: bool,
    config: QuotaPersistenceConfigLike | None,
    rate_limit: RateLimitWindow,
) -> str | None:
    """First reason (in check order) to skip persisting this result, or None to proceed.

    One ordered guard table for the boundary checks record_skill_result_rate_limit
    must apply before it may safely call record_observed_rate_limit — mirrors the
    checks that were previously five near-identical inline if/log/return blocks.
    """
    if config is None:
        return "no_quota_guard_config"
    if not supports_quota_check:
        return "backend_does_not_support_quota_check"
    if skill_result.infra.exit_category != InfraExitCategory.RATE_LIMITED.value:
        return "exit_category_not_rate_limited"
    if rate_limit.resets_at_epoch is None:
        return "rate_limit_resets_at_epoch is None"
    if not rate_limit.limit_type:
        return "rate_limit.limit_type is empty"
    return None


def record_skill_result_rate_limit(
    skill_result: SkillResult,
    supports_quota_check: bool,
    config: QuotaPersistenceConfigLike | None,
    *,
    now_epoch: int | None = None,
) -> None:
    """Project structured terminal reset evidence into the observed store.

    When _skill_result_rate_limit_skip_reason finds a boundary check fails (no
    config, quota checks unsupported by backend, infra exit not classified as
    RATE_LIMITED, or missing rate_limit fields) this is a meaningful decision —
    the PR's goal is "retain provider failure evidence", so silent drops
    undermine it. The skip logs at ``debug`` with the suppressed fields so an
    operator investigating why a 429 did not project into the observed store
    can find the reason without rerunning the session.
    """
    rate_limit = skill_result.api_failure.rate_limit
    skip_reason = _skill_result_rate_limit_skip_reason(
        skill_result=skill_result,
        supports_quota_check=supports_quota_check,
        config=config,
        rate_limit=rate_limit,
    )
    if skip_reason is not None:
        logger.debug(
            "quota_observed_evidence_skipped",
            skip_reason=skip_reason,
            exit_category=skill_result.infra.exit_category,
            supports_quota_check=supports_quota_check,
            resets_at_epoch=rate_limit.resets_at_epoch,
            limit_type=rate_limit.limit_type,
        )
        return
    # _skill_result_rate_limit_skip_reason returning None means every one of its
    # guards passed, which narrows both of these away from their Optional types --
    # spelled out explicitly since mypy cannot narrow across the function call.
    assert config is not None
    assert rate_limit.resets_at_epoch is not None
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
        # execution path that already classified this run as RATE_LIMITED. Stay
        # broad (never narrow this to a fixed tuple: acquire_flock_with_timeout
        # can raise TimeoutError under real lock contention, which must still be
        # swallowed here). Split severity via the shared operational-vs-bug tuple
        # so unexpected bugs (e.g. AttributeError from a malformed config) surface
        # at ERROR while routine I/O/lock failures stay at WARNING, mirroring
        # quota.py's check_and_sleep_if_needed fail-open boundary.
        if isinstance(exc, _OPERATIONAL_EXCEPTION_TYPES):
            logger.warning(
                "quota_observed_evidence_persist_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
        else:
            logger.error(
                "quota_observed_evidence_persist_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
