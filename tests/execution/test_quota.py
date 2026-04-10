"""Tests for execution/quota.py — credential reading, cache I/O, and check_and_sleep_if_needed."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from autoskillit.config.settings import QuotaGuardConfig


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
            "windows": {
                "five_hour": {
                    "utilization": 87.3,
                    "resets_at": "2026-02-27T20:15:00+00:00",
                }
            },
            "binding": {
                "window_name": "five_hour",
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
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _read_cache,
            _write_cache,
        )

        resets_at = datetime(2026, 2, 27, 20, 15, tzinfo=UTC)
        result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=75.0, resets_at=resets_at)},
            binding=QuotaStatus(utilization=75.0, resets_at=resets_at, window_name="five_hour"),
        )
        cache_path = tmp_path / "usage_cache.json"
        _write_cache(str(cache_path), result)
        recovered = _read_cache(str(cache_path), max_age=60)
        assert recovered is not None
        assert recovered.utilization == pytest.approx(75.0)

    def test_write_failure_does_not_raise(self, tmp_path):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _write_cache,
        )

        resets_at = datetime(2026, 2, 27, 20, 15, tzinfo=UTC)
        result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=50.0, resets_at=resets_at)},
            binding=QuotaStatus(utilization=50.0, resets_at=resets_at, window_name="five_hour"),
        )
        # Nonexistent parent directory — should log warning, not raise
        _write_cache("/nonexistent/dir/cache.json", result)


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
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def mock_fetch(path, **kwargs):
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=50.0, resets_at=resets_at)},
                binding=QuotaStatus(
                    utilization=50.0, resets_at=resets_at, window_name="five_hour"
                ),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert result["utilization"] == pytest.approx(50.0)

    @pytest.mark.anyio
    async def test_above_threshold_returns_should_sleep_true(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        def _make_result(util: float) -> QuotaFetchResult:
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=util, resets_at=resets_at)},
                binding=QuotaStatus(
                    utilization=util, resets_at=resets_at, window_name="five_hour"
                ),
            )

        mock_fetch = AsyncMock(side_effect=[_make_result(90.0), _make_result(91.0)])
        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert mock_fetch.call_count == 2
        assert result["sleep_seconds"] == pytest.approx(7200, abs=60)

    @pytest.mark.anyio
    async def test_uses_fresh_cache_skips_fetch(self, monkeypatch, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _write_cache,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=1)
        cache_path = tmp_path / "cache.json"
        _write_cache(
            str(cache_path),
            QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=40.0, resets_at=resets_at)},
                binding=QuotaStatus(
                    utilization=40.0, resets_at=resets_at, window_name="five_hour"
                ),
            ),
        )
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
            raise AssertionError("should not fetch when cache is fresh")

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
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        resets = datetime.now(UTC) + timedelta(hours=1)
        # First fetch: above threshold, has resets_at so Gate 1 passes
        first_result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=resets)},
            binding=QuotaStatus(utilization=90.0, resets_at=resets, window_name="five_hour"),
        )
        # Second fetch (re-fetch): above threshold, resets_at is None → Gate 2 fires
        second_result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
            binding=QuotaStatus(utilization=90.0, resets_at=None, window_name="five_hour"),
        )

        mock_fetch = AsyncMock(side_effect=[first_result, second_result])
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
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=60,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def mock_fetch(path, **kwargs):
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
                binding=QuotaStatus(utilization=90.0, resets_at=None, window_name="five_hour"),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0

    @pytest.mark.anyio
    async def test_above_threshold_resets_at_none_second_fetch_blocks(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=60,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        # First fetch has resets_at (passes first None guard), second fetch has None
        first_result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=resets_at)},
            binding=QuotaStatus(utilization=90.0, resets_at=resets_at, window_name="five_hour"),
        )
        second_result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
            binding=QuotaStatus(utilization=90.0, resets_at=None, window_name="five_hour"),
        )
        mock_fetch = AsyncMock(side_effect=[first_result, second_result])
        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0

    @pytest.mark.anyio
    async def test_cache_hit_resets_at_none_above_threshold_blocks(self, monkeypatch, tmp_path):
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _write_cache,
            check_and_sleep_if_needed,
        )

        cache_path = tmp_path / "cache.json"
        # Write a cache entry with resets_at=None and above-threshold utilization
        _write_cache(
            str(cache_path),
            QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
                binding=QuotaStatus(utilization=90.0, resets_at=None, window_name="five_hour"),
            ),
        )
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
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            buffer_seconds=120,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def mock_fetch(path, **kwargs):
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
                binding=QuotaStatus(utilization=90.0, resets_at=None, window_name="five_hour"),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] >= 120

    @pytest.mark.anyio
    async def test_above_threshold_with_buffer_seconds_default(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        # Do NOT override buffer_seconds — exercises the real default (60)
        config = QuotaGuardConfig(
            enabled=True,
            threshold=80.0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        def _r(util: float) -> QuotaFetchResult:
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=util, resets_at=resets_at)},
                binding=QuotaStatus(
                    utilization=util, resets_at=resets_at, window_name="five_hour"
                ),
            )

        mock_fetch = AsyncMock(side_effect=[_r(90.0), _r(90.0)])
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

        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _write_cache,
        )
        from autoskillit.hooks.quota_check import main

        cache_path = tmp_path / "quota_cache.json"
        _write_cache(
            str(cache_path),
            QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=95.0, resets_at=None)},
                binding=QuotaStatus(utilization=95.0, resets_at=None, window_name="five_hour"),
            ),
        )

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


class TestMultiWindowSelection:
    """Verifies _compute_binding selects the worst-case window and full-snapshot cache I/O."""

    # T-MW-1: one_hour exhausted, five_hour fine → binding = one_hour
    def test_binding_window_one_hour_exhausted(self):
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        resets_one_hour = now + timedelta(minutes=45)
        windows = {
            "one_hour": QuotaWindowEntry(utilization=91.0, resets_at=resets_one_hour),
            "five_hour": QuotaWindowEntry(utilization=35.0, resets_at=now + timedelta(hours=4)),
        }
        binding = _compute_binding(windows, threshold=85.0)
        assert binding.utilization == pytest.approx(91.0)
        assert binding.resets_at == resets_one_hour
        assert binding.window_name == "one_hour"

    # T-MW-2: multiple windows exhausted → binding = window with latest resets_at
    def test_binding_window_latest_resets_at_governs(self):
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        resets_one_hour = now + timedelta(minutes=45)
        resets_one_day = now + timedelta(hours=18)
        windows = {
            "one_hour": QuotaWindowEntry(utilization=91.0, resets_at=resets_one_hour),
            "one_day": QuotaWindowEntry(utilization=97.0, resets_at=resets_one_day),
            "five_hour": QuotaWindowEntry(utilization=35.0, resets_at=now + timedelta(hours=4)),
        }
        binding = _compute_binding(windows, threshold=85.0)
        assert binding.window_name == "one_day"
        assert binding.resets_at == resets_one_day

    # T-MW-3: all windows fine → should_sleep=False, binding = highest utilization
    def test_all_windows_fine_binding_is_highest_utilization(self):
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        five_hour_resets = now + timedelta(hours=4)
        windows = {
            "one_hour": QuotaWindowEntry(utilization=40.0, resets_at=now + timedelta(hours=1)),
            "five_hour": QuotaWindowEntry(utilization=60.0, resets_at=five_hour_resets),
            "one_day": QuotaWindowEntry(utilization=30.0, resets_at=now + timedelta(days=1)),
        }
        binding = _compute_binding(windows, threshold=85.0)
        assert binding.utilization == pytest.approx(60.0)
        assert binding.window_name == "five_hour"
        assert binding.resets_at == five_hour_resets

    # T-MW-3b: empty windows dict → returns zero-utilization sentinel, no ValueError
    def test_empty_windows_returns_zero_sentinel(self):
        from autoskillit.execution.quota import _compute_binding

        binding = _compute_binding({}, threshold=85.0)
        assert binding.utilization == pytest.approx(0.0)
        assert binding.resets_at is None

    # T-MW-4: _write_cache stores full windows dict + binding key
    def test_write_cache_stores_all_windows(self, tmp_path):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _write_cache,
        )

        now = datetime.now(UTC)
        resets = now + timedelta(hours=1)
        windows = {
            "one_hour": QuotaWindowEntry(utilization=91.0, resets_at=resets),
            "five_hour": QuotaWindowEntry(utilization=35.0, resets_at=now + timedelta(hours=4)),
        }
        binding = QuotaStatus(utilization=91.0, resets_at=resets, window_name="one_hour")
        result = QuotaFetchResult(windows=windows, binding=binding)
        cache_path = tmp_path / "cache.json"
        _write_cache(str(cache_path), result)
        data = json.loads(cache_path.read_text())
        assert "windows" in data
        assert "one_hour" in data["windows"]
        assert "five_hour" in data["windows"]
        assert "binding" in data
        assert data["binding"]["window_name"] == "one_hour"
        assert data["binding"]["utilization"] == pytest.approx(91.0)

    # T-MW-5: _read_cache with old-format cache (missing "binding") returns None
    def test_read_cache_old_format_returns_none(self, tmp_path):
        from autoskillit.execution.quota import _read_cache

        old_cache = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "five_hour": {"utilization": 87.0, "resets_at": None},
        }
        cache_path = tmp_path / "old_cache.json"
        cache_path.write_text(json.dumps(old_cache))
        result = _read_cache(str(cache_path), max_age=120)
        assert result is None

    # T-MW-6: _read_cache with new format returns QuotaStatus from binding
    def test_read_cache_new_format_returns_binding(self, tmp_path):
        from autoskillit.execution.quota import _read_cache

        new_cache = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "windows": {
                "one_hour": {
                    "utilization": 91.0,
                    "resets_at": "2026-04-10T09:45:00+00:00",
                },
                "five_hour": {
                    "utilization": 35.0,
                    "resets_at": "2026-04-10T13:00:00+00:00",
                },
            },
            "binding": {
                "window_name": "one_hour",
                "utilization": 91.0,
                "resets_at": "2026-04-10T09:45:00+00:00",
            },
        }
        cache_path = tmp_path / "new_cache.json"
        cache_path.write_text(json.dumps(new_cache))
        status = _read_cache(str(cache_path), max_age=120)
        assert status is not None
        assert status.utilization == pytest.approx(91.0)
        assert status.window_name == "one_hour"


class TestRefreshQuotaCache:
    """Tests for _refresh_quota_cache: unconditional fetch-and-write behavior."""

    @pytest.mark.anyio
    async def test_refresh_quota_cache_fetches_even_when_cache_is_fresh(
        self, tmp_path, monkeypatch
    ):
        """_refresh_quota_cache calls _fetch_quota unconditionally, even if cache is fresh."""
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _refresh_quota_cache,
        )

        fresh_cache = tmp_path / "cache.json"
        # Write a 10-second-old cache (well within 300s max_age)
        fresh_cache.write_text(
            json.dumps(
                {
                    "fetched_at": (datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
                    "five_hour": {"utilization": 0.3, "resets_at": None},
                }
            )
        )
        fetch_called = []

        async def fake_fetch(credentials_path, **kwargs):
            fetch_called.append(True)
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=0.35, resets_at=None)},
                binding=QuotaStatus(utilization=0.35, resets_at=None, window_name="five_hour"),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        config = QuotaGuardConfig(cache_path=str(fresh_cache))
        await _refresh_quota_cache(config)
        assert len(fetch_called) == 1  # must have fetched even though cache was fresh

    @pytest.mark.anyio
    async def test_refresh_quota_cache_writes_new_cache(self, tmp_path, monkeypatch):
        """_refresh_quota_cache writes a new cache file after fetching."""
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _refresh_quota_cache,
        )

        cache_path = tmp_path / "cache.json"

        async def fake_fetch(credentials_path, **kwargs):
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=0.5, resets_at=None)},
                binding=QuotaStatus(utilization=0.5, resets_at=None, window_name="five_hour"),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        config = QuotaGuardConfig(cache_path=str(cache_path))
        await _refresh_quota_cache(config)
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert data["binding"]["utilization"] == pytest.approx(0.5)
