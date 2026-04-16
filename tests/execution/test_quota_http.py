"""End-to-end HTTP tests for quota guard using api-simulator mock_http_server.

These tests exercise the real httpx client path — no monkeypatching of _fetch_quota.
They complement the unit tests in test_quota.py which mock at the function level.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from api_simulator.http import MockResponseSpec as PyResponseSpec

from autoskillit.execution.quota import check_and_sleep_if_needed

pytestmark = pytest.mark.anyio

QUOTA_ENDPOINT = "/api/oauth/usage"


@pytest.fixture()
def credentials(tmp_path):
    """Write a valid .credentials.json and return its path as a string."""
    creds_file = tmp_path / ".credentials.json"
    creds_file.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "test-token-abc123",
                    "expiresAt": (time.time() + 3600) * 1000,
                }
            }
        )
    )
    return str(creds_file)


@pytest.fixture()
def quota_config(credentials, tmp_path):
    """Minimal config namespace for check_and_sleep_if_needed."""
    return SimpleNamespace(
        enabled=True,
        short_window_enabled=True,
        long_window_enabled=True,
        credentials_path=credentials,
        cache_path=str(tmp_path / "quota_cache.json"),
        cache_max_age=120,
        short_window_threshold=80.0,
        long_window_threshold=98.0,
        long_window_patterns=["seven_day", "sonnet", "opus"],
        buffer_seconds=60,
    )


@pytest.fixture(autouse=True)
def _reset_mock(mock_http_server):
    """Reset mock_http_server before each test to clear routes and recordings."""
    mock_http_server.reset()


async def test_normal_utilization_returns_status_and_sends_correct_headers(
    mock_http_server, quota_config
):
    mock_http_server.register(
        "GET",
        QUOTA_ENDPOINT,
        PyResponseSpec(
            body={
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2026-04-05T00:00:00+00:00",
                }
            }
        ),
    )

    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)

    assert result["should_sleep"] is False
    assert result["utilization"] == 50.0
    assert result["resets_at"] == "2026-04-05T00:00:00+00:00"

    requests = mock_http_server.get_requests("GET", QUOTA_ENDPOINT)
    assert len(requests) == 1
    assert requests[0]["headers"]["authorization"] == "Bearer test-token-abc123"
    assert requests[0]["headers"]["anthropic-beta"] == "oauth-2025-04-20"


async def test_above_threshold_triggers_double_fetch(mock_http_server, quota_config):
    resets_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    mock_http_server.register_sequence(
        "GET",
        QUOTA_ENDPOINT,
        [
            PyResponseSpec(body={"five_hour": {"utilization": 95.0, "resets_at": resets_at}}),
            PyResponseSpec(body={"five_hour": {"utilization": 95.0, "resets_at": resets_at}}),
        ],
    )

    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)

    assert result["should_sleep"] is True
    assert result["sleep_seconds"] > 0
    assert mock_http_server.request_count("GET", QUOTA_ENDPOINT) == 2


async def test_resets_at_null_blocks_with_fallback(mock_http_server, quota_config):
    mock_http_server.register(
        "GET",
        QUOTA_ENDPOINT,
        PyResponseSpec(body={"five_hour": {"utilization": 95.0, "resets_at": None}}),
    )

    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)

    assert result["should_sleep"] is True
    assert result["sleep_seconds"] >= 60
    assert result["reason"] == "unknown_reset"
    assert mock_http_server.request_count("GET", QUOTA_ENDPOINT) == 1


async def test_http_429_fails_open(mock_http_server, quota_config):
    mock_http_server.register("GET", QUOTA_ENDPOINT, PyResponseSpec(status=429))

    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)

    assert result["should_sleep"] is False
    assert "error" in result


async def test_http_503_fails_open(mock_http_server, quota_config):
    mock_http_server.register("GET", QUOTA_ENDPOINT, PyResponseSpec(status=503))

    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)

    assert result["should_sleep"] is False
    assert "error" in result


async def test_network_timeout_fails_open(mock_http_server, quota_config):
    mock_http_server.register("GET", QUOTA_ENDPOINT, PyResponseSpec(body={}, delay_ms=500))

    result = await check_and_sleep_if_needed(
        quota_config, base_url=mock_http_server.url, _httpx_timeout=0.1
    )

    assert result["should_sleep"] is False
    assert "error" in result


async def test_z_suffix_resets_at_parsed_correctly(mock_http_server, quota_config):
    mock_http_server.register(
        "GET",
        QUOTA_ENDPOINT,
        PyResponseSpec(
            body={
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2026-04-05T00:00:00Z",
                }
            }
        ),
    )

    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)

    assert result["resets_at"] == "2026-04-05T00:00:00+00:00"


# T-HTTP-MW-1: multi-window response, worst-case window governs
async def test_multi_window_worst_case_governs(mock_http_server, quota_config):
    """When one_hour is exhausted but five_hour is fine, one_hour governs the sleep."""
    resets_at = (datetime.now(UTC) + timedelta(minutes=45)).isoformat()
    five_hour_resets = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
    mock_http_server.register_sequence(
        "GET",
        QUOTA_ENDPOINT,
        [
            PyResponseSpec(
                body={
                    "one_hour": {"utilization": 91.0, "resets_at": resets_at},
                    "five_hour": {"utilization": 35.0, "resets_at": five_hour_resets},
                }
            ),
            PyResponseSpec(
                body={
                    "one_hour": {"utilization": 91.0, "resets_at": resets_at},
                    "five_hour": {"utilization": 35.0, "resets_at": five_hour_resets},
                }
            ),
        ],
    )
    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)
    assert result["should_sleep"] is True
    assert result["sleep_seconds"] == pytest.approx(45 * 60 + 60, abs=30)
    assert result["window_name"] == "one_hour"


async def test_null_utilization_window_skipped(mock_http_server, quota_config):
    """Windows with utilization: null must be skipped, not crash."""
    mock_http_server.register(
        "GET",
        QUOTA_ENDPOINT,
        PyResponseSpec(
            body={
                "five_hour": {"utilization": None, "resets_at": "2026-04-10T12:00:00Z"},
                "daily": {"utilization": 50.0, "resets_at": "2026-04-10T18:00:00Z"},
            }
        ),
    )
    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)
    assert result["should_sleep"] is False
    assert result["utilization"] == 50.0


async def test_all_null_utilization_returns_zero(mock_http_server, quota_config):
    """When all windows have null utilization, result should be 0.0 (below threshold)."""
    mock_http_server.register(
        "GET",
        QUOTA_ENDPOINT,
        PyResponseSpec(
            body={
                "five_hour": {"utilization": None, "resets_at": None},
            }
        ),
    )
    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)
    assert result["should_sleep"] is False
    assert result["utilization"] == 0.0


# T-HTTP-MW-2: unknown/extra window keys are tolerated (forward compat)
async def test_unknown_window_keys_tolerated(mock_http_server, quota_config):
    """Extra unknown window keys in API response are tolerated without error."""
    mock_http_server.register(
        "GET",
        QUOTA_ENDPOINT,
        PyResponseSpec(
            body={
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2026-04-05T00:00:00+00:00",
                },
                "ten_year": {
                    "utilization": 1.0,
                    "resets_at": "2036-04-05T00:00:00+00:00",
                },
            }
        ),
    )
    result = await check_and_sleep_if_needed(quota_config, base_url=mock_http_server.url)
    assert result["should_sleep"] is False
    assert result["utilization"] == pytest.approx(50.0)


async def test_seven_day_raw_api_response_blocks_at_long_threshold(mock_http_server, credentials):
    """A realistic /api/oauth/usage response with seven_day at 99% must produce a block at 98%.

    Exercises the full path: raw API JSON → _fetch_quota parsing loop →
    _compute_binding classification → QuotaFetchResult. Unlike unit tests that mock
    _fetch_quota itself, this test validates the HTTP parsing layer end-to-end.
    """
    from autoskillit.config.settings import QuotaGuardConfig
    from autoskillit.execution.quota import _fetch_quota

    resets_at = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    mock_http_server.register(
        "GET",
        QUOTA_ENDPOINT,
        PyResponseSpec(
            body={
                "seven_day": {"utilization": 99.0, "resets_at": resets_at},
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
                },
            }
        ),
    )
    cfg = QuotaGuardConfig()

    result = await _fetch_quota(
        credentials,
        short_threshold=cfg.short_window_threshold,
        long_threshold=cfg.long_window_threshold,
        long_patterns=cfg.long_window_patterns,
        short_enabled=cfg.short_window_enabled,
        long_enabled=cfg.long_window_enabled,
        base_url=mock_http_server.url,
    )

    assert result.binding.should_block is True, (
        f"Expected should_block=True for seven_day at 99% with long_threshold=98.0, "
        f"got effective_threshold={result.binding.effective_threshold}, "
        f"window_name={result.binding.window_name!r}"
    )
    assert result.binding.window_name == "seven_day"
    assert result.binding.effective_threshold == 98.0
