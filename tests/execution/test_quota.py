"""Tests for execution/quota.py — credential reading, cache I/O, and check_and_sleep_if_needed."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest


class TestReadCredentials:
    def test_reads_access_token(self, tmp_path):
        expires_ms = int(time.time() * 1000) + 3_600_000
        creds = {"claudeAiOauth": {"accessToken": "tok-abc", "expiresAt": expires_ms}}
        path = tmp_path / ".credentials.json"
        path.write_text(json.dumps(creds))
        from autoskillit.execution.quota import _read_credentials

        assert _read_credentials(str(path)) == "tok-abc"

    def test_expired_token_raises(self, tmp_path):
        expires_ms = int(time.time() * 1000) - 1000  # already expired
        creds = {"claudeAiOauth": {"accessToken": "tok-abc", "expiresAt": expires_ms}}
        path = tmp_path / ".credentials.json"
        path.write_text(json.dumps(creds))
        from autoskillit.execution.quota import _read_credentials

        with pytest.raises(PermissionError):
            _read_credentials(str(path))

    def test_missing_file_raises(self, tmp_path):
        from autoskillit.execution.quota import _read_credentials

        with pytest.raises(FileNotFoundError):
            _read_credentials(str(tmp_path / "nonexistent.json"))

    def test_missing_key_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text('{"other": "data"}')
        from autoskillit.execution.quota import _read_credentials

        with pytest.raises(KeyError):
            _read_credentials(str(path))


class TestReadCache:
    def test_fresh_cache_returns_status(self, tmp_path):
        from autoskillit.execution.quota import _read_cache

        now = datetime.now(UTC)
        cache_data = {
            "fetched_at": (now - timedelta(seconds=30)).isoformat(),
            "five_hour": {
                "utilization": 87.3,
                "resets_at": "2026-02-27T20:15:00+00:00",
            },
        }
        cache_path = tmp_path / "usage_cache.json"
        cache_path.write_text(json.dumps(cache_data))
        status = _read_cache(str(cache_path), max_age=120)
        assert status is not None
        assert status.utilization == pytest.approx(87.3)

    def test_stale_cache_returns_none(self, tmp_path):
        from autoskillit.execution.quota import _read_cache

        now = datetime.now(UTC)
        cache_data = {
            "fetched_at": (now - timedelta(seconds=300)).isoformat(),
            "five_hour": {
                "utilization": 50.0,
                "resets_at": "2026-02-27T20:15:00+00:00",
            },
        }
        cache_path = tmp_path / "usage_cache.json"
        cache_path.write_text(json.dumps(cache_data))
        assert _read_cache(str(cache_path), max_age=120) is None

    def test_missing_cache_returns_none(self, tmp_path):
        from autoskillit.execution.quota import _read_cache

        assert _read_cache(str(tmp_path / "nonexistent.json"), max_age=120) is None

    def test_corrupted_cache_returns_none(self, tmp_path):
        from autoskillit.execution.quota import _read_cache

        cache_path = tmp_path / "usage_cache.json"
        cache_path.write_text("not valid json{{")
        assert _read_cache(str(cache_path), max_age=120) is None


class TestWriteCache:
    def test_writes_readable_cache(self, tmp_path):
        from autoskillit.execution.quota import QuotaStatus, _read_cache, _write_cache

        resets_at = datetime(2026, 2, 27, 20, 15, tzinfo=UTC)
        status = QuotaStatus(utilization=75.0, resets_at=resets_at)
        cache_path = tmp_path / "usage_cache.json"
        _write_cache(str(cache_path), status)
        recovered = _read_cache(str(cache_path), max_age=60)
        assert recovered is not None
        assert recovered.utilization == pytest.approx(75.0)

    def test_write_failure_does_not_raise(self, tmp_path):
        from autoskillit.execution.quota import QuotaStatus, _write_cache

        resets_at = datetime(2026, 2, 27, 20, 15, tzinfo=UTC)
        status = QuotaStatus(utilization=50.0, resets_at=resets_at)
        # Nonexistent parent directory — should log warning, not raise
        _write_cache("/nonexistent/dir/cache.json", status)


class TestCheckAndSleepIfNeeded:
    @pytest.mark.anyio
    async def test_disabled_returns_immediately_no_io(self, monkeypatch):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import check_and_sleep_if_needed

        config = QuotaGuardConfig(enabled=False)
        fetch_called = []

        async def mock_fetch(*a, **kw):
            fetch_called.append(1)
            raise AssertionError("should not fetch")

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert fetch_called == []

    @pytest.mark.anyio
    async def test_below_threshold_returns_should_sleep_false(self, monkeypatch, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import QuotaStatus, check_and_sleep_if_needed

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def mock_fetch(path, **kwargs):
            return QuotaStatus(utilization=50.0, resets_at=resets_at)

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert result["utilization"] == pytest.approx(50.0)

    @pytest.mark.anyio
    async def test_above_threshold_returns_should_sleep_true(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import QuotaStatus, check_and_sleep_if_needed

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        first_status = QuotaStatus(utilization=90.0, resets_at=resets_at)
        second_status = QuotaStatus(utilization=91.0, resets_at=resets_at)
        mock_fetch = AsyncMock(side_effect=[first_status, second_status])
        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert mock_fetch.call_count == 2
        assert result["sleep_seconds"] == pytest.approx(7200, abs=60)

    @pytest.mark.anyio
    async def test_uses_fresh_cache_skips_fetch(self, monkeypatch, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import (
            QuotaStatus,
            _write_cache,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=1)
        cache_path = tmp_path / "cache.json"
        _write_cache(str(cache_path), QuotaStatus(utilization=40.0, resets_at=resets_at))
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            cache_max_age=120,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(cache_path),
        )
        fetch_called = []

        async def mock_fetch(path):
            fetch_called.append(1)
            return QuotaStatus(utilization=99.9, resets_at=resets_at)

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert fetch_called == []
        assert result["should_sleep"] is False
        assert result["utilization"] == pytest.approx(40.0)

    @pytest.mark.anyio
    async def test_credentials_failure_returns_error_dict(self, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import check_and_sleep_if_needed

        config = QuotaGuardConfig(
            enabled=True,
            credentials_path=str(tmp_path / "nonexistent.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_network_error_returns_error_dict(self, monkeypatch, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import check_and_sleep_if_needed

        config = QuotaGuardConfig(
            enabled=True,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        expires_ms = int(time.time() * 1000) + 3_600_000
        (tmp_path / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "tok", "expiresAt": expires_ms}})
        )

        async def mock_fetch(path, **kwargs):
            raise OSError("network down")

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_resets_at_none_after_refetch_logs_warning_with_fallback(
        self, monkeypatch, tmp_path
    ):
        """Second resets_at-is-None guard (after re-fetch) must emit
        the 'blocking with fallback' warning."""
        from unittest.mock import AsyncMock

        import structlog.testing

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import QuotaStatus, check_and_sleep_if_needed

        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        # First fetch: above threshold, has resets_at so Gate 1 passes
        first_status = QuotaStatus(
            utilization=90.0, resets_at=datetime.now(UTC) + timedelta(hours=1)
        )
        # Second fetch (re-fetch): above threshold, resets_at is None → Gate 2 fires
        second_status = QuotaStatus(utilization=90.0, resets_at=None)

        mock_fetch = AsyncMock(side_effect=[first_status, second_status])
        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)

        with structlog.testing.capture_logs() as cap:
            result = await check_and_sleep_if_needed(config)

        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0
        assert mock_fetch.call_count == 2
        # Exact message required by the rectify plan
        assert any(
            "quota above threshold but resets_at is None after re-fetch — blocking with fallback"
            in rec.get("event", "")
            for rec in cap
        )


class TestCheckAndSleepResetAtNoneBlocks:
    @pytest.mark.anyio
    async def test_above_threshold_resets_at_none_first_fetch_blocks(self, monkeypatch, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import QuotaStatus, check_and_sleep_if_needed

        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=60,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def mock_fetch(path, **kwargs):
            return QuotaStatus(utilization=90.0, resets_at=None)

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0

    @pytest.mark.anyio
    async def test_above_threshold_resets_at_none_second_fetch_blocks(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import QuotaStatus, check_and_sleep_if_needed

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=60,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        # First fetch has resets_at (passes first None guard), second fetch has None
        first_status = QuotaStatus(utilization=90.0, resets_at=resets_at)
        second_status = QuotaStatus(utilization=90.0, resets_at=None)
        mock_fetch = AsyncMock(side_effect=[first_status, second_status])
        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0

    @pytest.mark.anyio
    async def test_cache_hit_resets_at_none_above_threshold_blocks(self, monkeypatch, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import (
            QuotaStatus,
            _write_cache,
            check_and_sleep_if_needed,
        )

        cache_path = tmp_path / "cache.json"
        # Write a cache entry with resets_at=None and above-threshold utilization
        _write_cache(str(cache_path), QuotaStatus(utilization=90.0, resets_at=None))
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=60,
            cache_max_age=120,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(cache_path),
        )
        fetch_called = []

        async def mock_fetch(path):
            fetch_called.append(1)
            raise AssertionError("should not reach re-fetch when first branch blocks")

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert fetch_called == []

    @pytest.mark.anyio
    async def test_fallback_sleep_uses_at_least_buffer_seconds(self, monkeypatch, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import QuotaStatus, check_and_sleep_if_needed

        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=120,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def mock_fetch(path, **kwargs):
            return QuotaStatus(utilization=90.0, resets_at=None)

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] >= 120

    @pytest.mark.anyio
    async def test_above_threshold_with_buffer_seconds_default(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import QuotaStatus, check_and_sleep_if_needed

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        # Do NOT override buffer_seconds — exercises the real default (60)
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        first_status = QuotaStatus(utilization=90.0, resets_at=resets_at)
        second_status = QuotaStatus(utilization=90.0, resets_at=resets_at)
        mock_fetch = AsyncMock(side_effect=[first_status, second_status])
        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0


class TestIntegration:
    """Integration tests: write/read contract between execution.quota and hooks.quota_check."""

    def test_write_cache_then_quota_check_main_reads_it(self, tmp_path, monkeypatch):
        """Cache written by _write_cache must be readable and actionable by quota_check.main().

        T-INT-1: Catches format drift between _write_cache (execution layer) and
        _read_quota_cache (hook subprocess layer).
        """
        import io
        from contextlib import redirect_stdout

        from autoskillit.execution.quota import QuotaStatus, _write_cache
        from autoskillit.hooks.quota_check import main

        cache_path = tmp_path / "quota_cache.json"
        _write_cache(str(cache_path), QuotaStatus(utilization=95.0, resets_at=None))

        stdin_text = json.dumps({"tool_name": "run_skill"})
        buf = io.StringIO()
        with patch("sys.stdin", io.StringIO(stdin_text)):
            with redirect_stdout(buf):
                try:
                    main(cache_path_override=str(cache_path))
                except SystemExit:
                    pass

        out = buf.getvalue()
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
