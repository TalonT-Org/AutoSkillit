"""Observed terminal resets remain durable across quota polls."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from autoskillit.execution.quota import (
    QuotaFetchResult,
    QuotaStatus,
    QuotaWindowEntry,
    _write_cache,
    check_and_sleep_if_needed,
    record_observed_rate_limit,
)
from autoskillit.quota_constraints import (
    decode_observed_constraints,
    observed_constraint_path,
    quota_scope,
)
from tests._helpers import make_quota_guard_config

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _config(tmp_path, name="cache.json"):
    return make_quota_guard_config(
        enabled=True,
        buffer_seconds=0,
        credentials_path=str(tmp_path / ".credentials.json"),
        cache_path=str(tmp_path / name),
    )


def _scope(config) -> str:
    from pathlib import Path

    return quota_scope("anthropic", Path(config.credentials_path).expanduser())


@pytest.mark.anyio
async def test_observation_survives_nonblocking_poll_cache(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path)
    now = int(datetime.now(UTC).timestamp())
    record_observed_rate_limit(
        config,
        scope=_scope(config),
        resets_at_epoch=now + 3600,
        limit_type="seven_day",
        now_epoch=now,
    )
    _write_cache(
        config.cache_path,
        QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(20, None)},
            binding=QuotaStatus(20, None, "five_hour", False, 85),
        ),
    )
    monkeypatch.setattr(
        "autoskillit.execution.quota._fetch_quota",
        lambda *args, **kwargs: pytest.fail("fresh nonblocking cache must not fetch"),
    )
    result = await check_and_sleep_if_needed(config)
    assert result["should_sleep"] is True
    assert result["block_source"] == "observed_terminal"
    assert datetime.fromisoformat(result["resets_at"]).tzinfo is not None
    assert decode_observed_constraints(observed_constraint_path(config.cache_path))


@pytest.mark.anyio
async def test_cross_dispatch_observation_suppresses_without_fetch(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path)
    now = int(datetime.now(UTC).timestamp())
    record_observed_rate_limit(
        config,
        scope=_scope(config),
        resets_at_epoch=now + 3600,
        limit_type="seven_day",
        now_epoch=now,
    )

    async def fail_fetch(*args, **kwargs):
        raise AssertionError("observation must suppress before network fetch")

    monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fail_fetch)
    assert (await check_and_sleep_if_needed(config))["should_sleep"] is True
    assert (await check_and_sleep_if_needed(config))["should_sleep"] is True


@pytest.mark.anyio
async def test_blocking_cache_refetch_cannot_clobber_observation(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path)
    now_dt = datetime.now(UTC)
    now = int(now_dt.timestamp())
    record_observed_rate_limit(
        config,
        scope=_scope(config),
        resets_at_epoch=now + 7200,
        limit_type="seven_day",
        now_epoch=now,
    )
    _write_cache(
        config.cache_path,
        QuotaFetchResult(
            binding=QuotaStatus(90, now_dt + timedelta(hours=1), "five_hour", True, 85)
        ),
    )

    async def refreshed(*args, **kwargs):
        return QuotaFetchResult(binding=QuotaStatus(10, None, "five_hour", False, 85))

    monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", refreshed)
    result = await check_and_sleep_if_needed(config)
    assert result["block_source"] == "observed_terminal"
    assert json.loads(observed_constraint_path(config.cache_path).read_text())["constraints"]


def test_expired_constraints_prune_on_next_write_and_scope_isolated(tmp_path) -> None:
    config = _config(tmp_path)
    record_observed_rate_limit(
        config, scope="other", resets_at_epoch=90, limit_type="one_day", now_epoch=80
    )
    record_observed_rate_limit(
        config, scope=_scope(config), resets_at_epoch=200, limit_type="seven_day", now_epoch=100
    )
    constraints = decode_observed_constraints(observed_constraint_path(config.cache_path))
    assert all(item.blocked_until_epoch > 100 for item in constraints)
    assert {item.scope for item in constraints} == {_scope(config)}


def test_concurrent_observations_are_not_lost(tmp_path) -> None:
    config = _config(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda offset: record_observed_rate_limit(
                    config,
                    scope=_scope(config),
                    resets_at_epoch=1000 + offset,
                    limit_type=f"window-{offset}",
                    now_epoch=100,
                ),
                range(8),
            )
        )
    constraints = decode_observed_constraints(observed_constraint_path(config.cache_path))
    assert {item.limit_type for item in constraints} == {f"window-{offset}" for offset in range(8)}
