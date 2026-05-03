"""Tests for execution/quota.py — check_and_sleep_if_needed, resets_at-None blocking,
integration, and novel window warnings."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tests._helpers import make_quota_guard_config

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class TestCheckAndSleepIfNeeded:
    @pytest.mark.anyio
    async def test_disabled_returns_immediately_no_io(self, monkeypatch):
        from autoskillit.execution.quota import check_and_sleep_if_needed

        config = make_quota_guard_config(enabled=False)
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
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = make_quota_guard_config(
            enabled=True,
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

        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = make_quota_guard_config(
            enabled=True,
            buffer_seconds=0,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        def _make_result(util: float) -> QuotaFetchResult:
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=util, resets_at=resets_at)},
                binding=QuotaStatus(
                    utilization=util,
                    resets_at=resets_at,
                    window_name="five_hour",
                    should_block=util >= 85.0,
                    effective_threshold=85.0,
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
        config = make_quota_guard_config(
            enabled=True,
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
        from autoskillit.execution.quota import check_and_sleep_if_needed

        config = make_quota_guard_config(
            enabled=True,
            credentials_path=str(tmp_path / "nonexistent.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert "error" in result

    @pytest.mark.anyio
    async def test_network_error_returns_error_dict(self, monkeypatch, tmp_path):
        from autoskillit.execution.quota import check_and_sleep_if_needed

        config = make_quota_guard_config(
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

        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        config = make_quota_guard_config(
            enabled=True,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        resets = datetime.now(UTC) + timedelta(hours=1)
        # First fetch: above threshold, has resets_at so Gate 1 passes
        first_result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=resets)},
            binding=QuotaStatus(
                utilization=90.0,
                resets_at=resets,
                window_name="five_hour",
                should_block=True,
                effective_threshold=85.0,
            ),
        )
        # Second fetch (re-fetch): above threshold, resets_at is None → Gate 2 fires
        second_result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
            binding=QuotaStatus(
                utilization=90.0,
                resets_at=None,
                window_name="five_hour",
                should_block=True,
                effective_threshold=85.0,
            ),
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
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        config = make_quota_guard_config(
            enabled=True,
            buffer_seconds=60,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def mock_fetch(path, **kwargs):
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
                binding=QuotaStatus(
                    utilization=90.0,
                    resets_at=None,
                    window_name="five_hour",
                    should_block=True,
                    effective_threshold=85.0,
                ),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0

    @pytest.mark.anyio
    async def test_above_threshold_resets_at_none_second_fetch_blocks(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        config = make_quota_guard_config(
            enabled=True,
            buffer_seconds=60,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        # First fetch has resets_at (passes first None guard), second fetch has None
        first_result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=resets_at)},
            binding=QuotaStatus(
                utilization=90.0,
                resets_at=resets_at,
                window_name="five_hour",
                should_block=True,
                effective_threshold=85.0,
            ),
        )
        second_result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
            binding=QuotaStatus(
                utilization=90.0,
                resets_at=None,
                window_name="five_hour",
                should_block=True,
                effective_threshold=85.0,
            ),
        )
        mock_fetch = AsyncMock(side_effect=[first_result, second_result])
        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0

    @pytest.mark.anyio
    async def test_cache_hit_resets_at_none_above_threshold_blocks(self, monkeypatch, tmp_path):
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
                binding=QuotaStatus(
                    utilization=90.0,
                    resets_at=None,
                    window_name="five_hour",
                    should_block=True,
                    effective_threshold=85.0,
                ),
            ),
        )
        config = make_quota_guard_config(
            enabled=True,
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
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        config = make_quota_guard_config(
            enabled=True,
            buffer_seconds=120,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def mock_fetch(path, **kwargs):
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=90.0, resets_at=None)},
                binding=QuotaStatus(
                    utilization=90.0,
                    resets_at=None,
                    window_name="five_hour",
                    should_block=True,
                    effective_threshold=85.0,
                ),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] >= 120

    @pytest.mark.anyio
    async def test_above_threshold_with_buffer_seconds_default(self, monkeypatch, tmp_path):
        from unittest.mock import AsyncMock

        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=2)
        # Do NOT override buffer_seconds — exercises the real default (60)
        config = make_quota_guard_config(
            enabled=True,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        def _r(util: float) -> QuotaFetchResult:
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=util, resets_at=resets_at)},
                binding=QuotaStatus(
                    utilization=util,
                    resets_at=resets_at,
                    window_name="five_hour",
                    should_block=util >= 85.0,
                    effective_threshold=85.0,
                ),
            )

        mock_fetch = AsyncMock(side_effect=[_r(90.0), _r(90.0)])
        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", mock_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["sleep_seconds"] > 0


class TestIntegration:
    """Integration tests: write/read contract between execution.quota and hooks.quota_guard."""

    def test_write_cache_then_quota_check_main_reads_it(self, tmp_path, monkeypatch):
        """Cache written by _write_cache must be readable and actionable by quota_guard.main().

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
        from autoskillit.hooks.guards.quota_guard import main

        cache_path = tmp_path / "quota_cache.json"
        _write_cache(
            str(cache_path),
            QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=95.0, resets_at=None)},
                binding=QuotaStatus(
                    utilization=95.0,
                    resets_at=None,
                    window_name="five_hour",
                    should_block=True,
                    effective_threshold=85.0,
                ),
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


class TestFetchQuotaNovelWindowWarning:
    """_fetch_quota must log a warning when it encounters a window name not in
    KNOWN_QUOTA_WINDOW_NAMES. This surfaces Anthropic API vocabulary drift in
    operator logs without disrupting the pipeline."""

    @staticmethod
    def _make_fake_httpx_client(api_response: dict):
        """Return a fake httpx.AsyncClient instance that serves api_response for GET requests."""

        class FakeResponse:
            status_code = 200

            def json(self):
                return api_response

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, *a, **kw):
                return FakeResponse()

        return FakeClient()

    @pytest.mark.anyio
    async def test_novel_window_name_logs_warning(self, monkeypatch):
        """An unknown window name in the API response must produce a warning log entry."""
        import structlog.testing

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import _fetch_quota

        resets_at = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
        api_response = {
            "five_hour": {"utilization": 10.0, "resets_at": resets_at},
            "fortnight": {"utilization": 50.0, "resets_at": resets_at},  # unknown
        }
        cfg = QuotaGuardConfig()

        monkeypatch.setattr(
            "httpx.AsyncClient", lambda **kw: self._make_fake_httpx_client(api_response)
        )
        monkeypatch.setattr(
            "autoskillit.execution.quota._read_credentials",
            lambda path: "fake-token",
        )

        with structlog.testing.capture_logs() as cap:
            await _fetch_quota(
                cfg.credentials_path,
                short_threshold=cfg.short_window_threshold,
                long_threshold=cfg.long_window_threshold,
                long_patterns=cfg.long_window_patterns,
                short_enabled=cfg.short_window_enabled,
                long_enabled=cfg.long_window_enabled,
            )

        novel_warning_records = [
            rec for rec in cap if rec.get("log_level") == "warning" and "novel_windows" in rec
        ]
        assert any(
            "fortnight" in str(rec.get("novel_windows", [])) for rec in novel_warning_records
        ), (
            f"Expected a warning with 'fortnight' in novel_windows for unknown window name. "
            f"Got all captured records: {cap}"
        )

    @pytest.mark.anyio
    async def test_all_known_windows_do_not_log_warning(self, monkeypatch):
        """No warning must be logged when all API window names are in KNOWN_QUOTA_WINDOW_NAMES."""
        import structlog.testing

        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import KNOWN_QUOTA_WINDOW_NAMES, _fetch_quota

        resets_at = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
        api_response = {
            name: {"utilization": 10.0, "resets_at": resets_at}
            for name in KNOWN_QUOTA_WINDOW_NAMES
        }
        cfg = QuotaGuardConfig()

        monkeypatch.setattr(
            "httpx.AsyncClient", lambda **kw: self._make_fake_httpx_client(api_response)
        )
        monkeypatch.setattr(
            "autoskillit.execution.quota._read_credentials",
            lambda path: "fake-token",
        )

        with structlog.testing.capture_logs() as cap:
            await _fetch_quota(
                cfg.credentials_path,
                short_threshold=cfg.short_window_threshold,
                long_threshold=cfg.long_window_threshold,
                long_patterns=cfg.long_window_patterns,
                short_enabled=cfg.short_window_enabled,
                long_enabled=cfg.long_window_enabled,
            )

        novel_warnings = [
            rec for rec in cap if rec.get("log_level") == "warning" and "novel_windows" in rec
        ]
        assert not novel_warnings, (
            f"Unexpected novel-window warnings for known windows: {novel_warnings}"
        )
