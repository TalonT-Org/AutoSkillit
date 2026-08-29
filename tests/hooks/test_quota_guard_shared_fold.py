"""Hook and Python quota gates share cumulative constraint semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autoskillit.execution.quota import (
    QuotaFetchResult,
    QuotaStatus,
    _write_cache,
    record_observed_rate_limit,
)
from autoskillit.hooks._hook_settings import QuotaHookSettings
from autoskillit.hooks.guards.quota_guard import quota_guard_decision
from autoskillit.hooks.quota_post_hook import quota_post_decision
from autoskillit.quota_constraints import quota_scope
from tests._helpers import make_quota_guard_config

pytestmark = pytest.mark.medium


def test_hooks_fold_observed_and_poll_constraints(tmp_path) -> None:
    credentials = tmp_path / ".credentials.json"
    cache = tmp_path / "cache.json"
    scope = quota_scope("anthropic", credentials)
    config = make_quota_guard_config(credentials_path=str(credentials), cache_path=str(cache))
    now = int(datetime.now(UTC).timestamp())
    record_observed_rate_limit(
        config,
        scope=scope,
        resets_at_epoch=now + 3600,
        limit_type="seven_day",
        now_epoch=now,
    )
    settings = QuotaHookSettings(str(cache), 300, 60, scope)

    guard_winner, _ = quota_guard_decision(settings, now_epoch=now)
    post_winner, _ = quota_post_decision(settings, now_epoch=now)
    assert guard_winner == post_winner
    assert guard_winner is not None
    assert guard_winner.blocked_until_epoch == now + 3600

    poll_reset = datetime.now(UTC) + timedelta(hours=2)
    _write_cache(
        str(cache),
        QuotaFetchResult(binding=QuotaStatus(90, poll_reset, "five_hour", True, 85)),
    )
    guard_winner, _ = quota_guard_decision(settings, now_epoch=now)
    post_winner, _ = quota_post_decision(settings, now_epoch=now)
    assert guard_winner == post_winner
    assert guard_winner is not None
    assert guard_winner.source.value == "provider_poll"

    _write_cache(
        str(cache),
        QuotaFetchResult(binding=QuotaStatus(20, None, "five_hour", False, 85)),
    )
    guard_winner, _ = quota_guard_decision(settings, now_epoch=now)
    assert guard_winner is not None
    assert guard_winner.source.value == "observed_terminal"
