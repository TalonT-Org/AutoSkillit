"""The headless convergence point records structured rate-limit resets."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    ApiFailureOutcome,
    InfraExitCategory,
    InfraOutcome,
    KillReason,
    RateLimitWindow,
    RetryReason,
    SkillResult,
)
from autoskillit.execution.quota import record_skill_result_rate_limit
from autoskillit.quota_constraints import (
    decode_observed_constraints,
    observed_constraint_path,
    quota_scope,
)
from tests._helpers import make_quota_guard_config

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _rate_limited_result(subtype: str, epoch: int | None) -> SkillResult:
    return SkillResult(
        success=False,
        result="",
        session_id=f"session-{subtype}",
        subtype=subtype,
        is_error=True,
        exit_code=1,
        needs_retry=True,
        retry_reason=RetryReason.RATE_LIMITED,
        stderr="",
        kill_reason=KillReason.NATURAL_EXIT,
        infra=InfraOutcome(exit_category=InfraExitCategory.RATE_LIMITED.value),
        api_failure=ApiFailureOutcome(
            status=429,
            rate_limit=RateLimitWindow(
                status="rejected",
                limit_type="seven_day",
                resets_at_epoch=epoch,
            ),
        ),
    )


@pytest.mark.parametrize("termination", ["normal", "stale", "idle_stall"])
def test_all_rate_limited_terminations_record_at_convergence(tmp_path, termination) -> None:
    config = make_quota_guard_config(
        credentials_path=str(tmp_path / ".credentials.json"),
        cache_path=str(tmp_path / "cache.json"),
    )
    epoch = 2_000_000_000
    record_skill_result_rate_limit(
        _rate_limited_result(termination, epoch),
        True,
        config,
        now_epoch=1_900_000_000,
    )
    constraints = decode_observed_constraints(observed_constraint_path(config.cache_path))
    assert len(constraints) == 1
    assert constraints[0].blocked_until_epoch == epoch
    assert constraints[0].scope == quota_scope(
        "anthropic", Path(config.credentials_path).expanduser()
    )
    assert constraints[0].source.value == "observed_terminal"


def test_rate_limit_without_structured_epoch_records_nothing(tmp_path) -> None:
    config = make_quota_guard_config(
        credentials_path=str(tmp_path / ".credentials.json"),
        cache_path=str(tmp_path / "cache.json"),
    )
    record_skill_result_rate_limit(
        _rate_limited_result("normal", None),
        True,
        config,
        now_epoch=1_900_000_000,
    )
    assert not observed_constraint_path(config.cache_path).exists()
