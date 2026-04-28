"""Tests for execution/quota.py — multi-window selection, per-window thresholds, cache refresh, per-window toggles, and API vocabulary contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests._helpers import make_quota_guard_config

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


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
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=["weekly", "sonnet", "opus"],
        )
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
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=["weekly", "sonnet", "opus"],
        )
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
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=["weekly", "sonnet", "opus"],
        )
        assert binding.utilization == pytest.approx(60.0)
        assert binding.window_name == "five_hour"
        assert binding.resets_at == five_hour_resets

    # T-MW-3b: empty windows dict → returns zero-utilization sentinel, no ValueError
    def test_empty_windows_returns_zero_sentinel(self):
        from autoskillit.execution.quota import _compute_binding

        binding = _compute_binding(
            {},
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=["weekly", "sonnet", "opus"],
        )
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
            "schema_version": 3,
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
                "should_block": True,
                "effective_threshold": 85.0,
            },
        }
        cache_path = tmp_path / "new_cache.json"
        cache_path.write_text(json.dumps(new_cache))
        status = _read_cache(str(cache_path), max_age=120)
        assert status is not None
        assert status.utilization == pytest.approx(91.0)
        assert status.window_name == "one_hour"

    def test_compute_binding_blocks_seven_day_above_long_threshold(self):
        """Full _compute_binding path: seven_day at 99% must block at long threshold."""
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        cfg = QuotaGuardConfig()
        now = datetime.now(UTC)
        windows = {
            "seven_day": QuotaWindowEntry(
                utilization=99.0,
                resets_at=now + timedelta(days=3),
            ),
            "five_hour": QuotaWindowEntry(
                utilization=10.0,
                resets_at=now + timedelta(hours=3),
            ),
        }
        result = _compute_binding(
            windows,
            short_threshold=cfg.short_window_threshold,
            long_threshold=cfg.long_window_threshold,
            long_patterns=cfg.long_window_patterns,
            short_enabled=cfg.short_window_enabled,
            long_enabled=cfg.long_window_enabled,
        )
        assert result.should_block is True
        assert result.window_name == "seven_day"
        assert result.effective_threshold == 95.0


class TestPerWindowThresholds:
    """Per-window threshold classification: short windows block at 85%, long at 98%."""

    _LONG_PATTERNS = ["seven_day", "sonnet", "opus"]

    def test_threshold_for_window_short_default(self):
        from autoskillit.execution.quota import _threshold_for_window

        assert (
            _threshold_for_window(
                "five_hour",
                short_threshold=85.0,
                long_threshold=98.0,
                long_patterns=self._LONG_PATTERNS,
            )
            == 85.0
        )

    def test_threshold_for_window_long_seven_day(self):
        from autoskillit.execution.quota import _threshold_for_window

        assert (
            _threshold_for_window(
                "seven_day",
                short_threshold=85.0,
                long_threshold=98.0,
                long_patterns=self._LONG_PATTERNS,
            )
            == 98.0
        )

    def test_threshold_for_window_long_sonnet_substring(self):
        from autoskillit.execution.quota import _threshold_for_window

        assert (
            _threshold_for_window(
                "weekly_sonnet",
                short_threshold=85.0,
                long_threshold=98.0,
                long_patterns=self._LONG_PATTERNS,
            )
            == 98.0
        )

    def test_threshold_for_window_case_insensitive(self):
        from autoskillit.execution.quota import _threshold_for_window

        assert (
            _threshold_for_window(
                "Seven_Day",
                short_threshold=85.0,
                long_threshold=98.0,
                long_patterns=self._LONG_PATTERNS,
            )
            == 98.0
        )

    def test_threshold_for_window_unknown_uses_short(self):
        from autoskillit.execution.quota import _threshold_for_window

        assert (
            _threshold_for_window(
                "context",
                short_threshold=85.0,
                long_threshold=98.0,
                long_patterns=self._LONG_PATTERNS,
            )
            == 85.0
        )

    def test_compute_binding_picks_short_when_only_short_exhausted(self):
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        windows = {
            "five_hour": QuotaWindowEntry(utilization=90.0, resets_at=now + timedelta(hours=1)),
            "weekly": QuotaWindowEntry(utilization=80.0, resets_at=now + timedelta(days=4)),
        }
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
        )
        assert binding.window_name == "five_hour"
        assert binding.should_block is True
        assert binding.effective_threshold == pytest.approx(85.0)

    def test_compute_binding_does_not_block_seven_day_below_long_threshold(self):
        """Regression test for issue #721: seven_day at 86% must not block.

        Long-window quotas (seven_day, sonnet, opus) reset across multi-day windows,
        so 14% remaining headroom is comfortable, not exhausted. The pre-fix
        behaviour blocked the pipeline for ~4 days at 86% seven_day.
        """
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        windows = {
            "seven_day": QuotaWindowEntry(utilization=86.0, resets_at=now + timedelta(days=4)),
            "five_hour": QuotaWindowEntry(utilization=50.0, resets_at=now + timedelta(hours=1)),
        }
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
        )
        assert binding.should_block is False
        assert binding.effective_threshold == pytest.approx(98.0)

    def test_compute_binding_blocks_seven_day_at_99_percent(self):
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        windows = {
            "seven_day": QuotaWindowEntry(utilization=99.0, resets_at=now + timedelta(days=4)),
        }
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
        )
        assert binding.should_block is True
        assert binding.effective_threshold == pytest.approx(98.0)

    def test_compute_binding_picks_latest_resets_among_exhausted(self):
        """Among exhausted windows, the one with the latest reset wins."""
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        weekly_resets = now + timedelta(days=4)
        windows = {
            "five_hour": QuotaWindowEntry(utilization=90.0, resets_at=now + timedelta(hours=1)),
            "weekly": QuotaWindowEntry(utilization=99.0, resets_at=weekly_resets),
        }
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
        )
        assert binding.window_name == "weekly"
        assert binding.resets_at == weekly_resets
        assert binding.should_block is True

    def test_write_cache_includes_should_block_and_effective_threshold(self, tmp_path):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _write_cache,
        )

        now = datetime.now(UTC)
        weekly_resets = now + timedelta(days=4)
        windows = {
            "weekly": QuotaWindowEntry(utilization=86.0, resets_at=weekly_resets),
        }
        binding = QuotaStatus(
            utilization=86.0,
            resets_at=weekly_resets,
            window_name="weekly",
            should_block=False,
            effective_threshold=98.0,
        )
        result = QuotaFetchResult(windows=windows, binding=binding)
        cache_path = tmp_path / "cache.json"
        _write_cache(str(cache_path), result)
        data = json.loads(cache_path.read_text())
        assert data["binding"]["should_block"] is False
        assert data["binding"]["effective_threshold"] == pytest.approx(98.0)

    def test_read_cache_round_trip_should_block(self, tmp_path):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _read_cache,
            _write_cache,
        )

        now = datetime.now(UTC)
        weekly_resets = now + timedelta(days=4)
        windows = {
            "seven_day": QuotaWindowEntry(utilization=99.0, resets_at=weekly_resets),
        }
        binding = QuotaStatus(
            utilization=99.0,
            resets_at=weekly_resets,
            window_name="seven_day",
            should_block=True,
            effective_threshold=98.0,
        )
        cache_path = tmp_path / "cache.json"
        _write_cache(str(cache_path), QuotaFetchResult(windows=windows, binding=binding))
        status = _read_cache(str(cache_path), max_age=300)
        assert status is not None
        assert status.should_block is True
        assert status.effective_threshold == pytest.approx(98.0)
        assert status.window_name == "seven_day"

    @pytest.mark.anyio
    async def test_check_and_sleep_returns_false_for_seven_day_at_86_percent(
        self, monkeypatch, tmp_path
    ):
        """End-to-end regression test for #721 — seven_day at 86% must not sleep."""
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaWindowEntry,
            _compute_binding,
            check_and_sleep_if_needed,
        )

        now = datetime.now(UTC)
        windows = {
            "seven_day": QuotaWindowEntry(utilization=86.0, resets_at=now + timedelta(days=4)),
            "five_hour": QuotaWindowEntry(utilization=2.0, resets_at=now + timedelta(hours=1)),
        }
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
        )

        async def fake_fetch(credentials_path, **kwargs):
            return QuotaFetchResult(windows=windows, binding=binding)

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        config = make_quota_guard_config(
            cache_path=str(tmp_path / "cache.json"),
            credentials_path=str(tmp_path / "creds.json"),
        )
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert result["window_name"] == "seven_day"

    @pytest.mark.anyio
    async def test_check_and_sleep_returns_true_for_seven_day_at_99_percent(
        self, monkeypatch, tmp_path
    ):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaWindowEntry,
            _compute_binding,
            check_and_sleep_if_needed,
        )

        now = datetime.now(UTC)
        weekly_resets = now + timedelta(days=4)
        windows = {
            "seven_day": QuotaWindowEntry(utilization=99.0, resets_at=weekly_resets),
            "five_hour": QuotaWindowEntry(utilization=2.0, resets_at=now + timedelta(hours=1)),
        }
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
        )

        async def fake_fetch(credentials_path, **kwargs):
            return QuotaFetchResult(windows=windows, binding=binding)

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        config = make_quota_guard_config(
            cache_path=str(tmp_path / "cache.json"),
            credentials_path=str(tmp_path / "creds.json"),
        )
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is True
        assert result["window_name"] == "seven_day"
        assert result["sleep_seconds"] > 0

    def test_seven_day_window_classified_as_long_with_default_patterns(self):
        """seven_day is the actual Anthropic API key for the weekly budget.
        It must be classified as a long window by the default long_window_patterns."""
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import _is_long_window

        defaults = QuotaGuardConfig().long_window_patterns
        assert _is_long_window("seven_day", defaults), (
            f"seven_day not classified as long by default patterns {defaults!r}. "
            "Update long_window_patterns to include a pattern that matches 'seven_day'."
        )

    def test_threshold_for_window_seven_day_returns_long_threshold(self):
        """seven_day must yield the long threshold (95.0) with default config."""
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import _threshold_for_window

        cfg = QuotaGuardConfig()
        result = _threshold_for_window(
            "seven_day",
            short_threshold=cfg.short_window_threshold,
            long_threshold=cfg.long_window_threshold,
            long_patterns=cfg.long_window_patterns,
        )
        assert result == 95.0, (
            f"seven_day returned threshold {result}, expected 95.0. "
            "The Anthropic API uses 'seven_day' for the 7-day rate-limit window."
        )


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
        config = make_quota_guard_config(cache_path=str(fresh_cache))
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
        config = make_quota_guard_config(cache_path=str(cache_path))
        await _refresh_quota_cache(config)
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert data["binding"]["utilization"] == pytest.approx(0.5)


class TestPerWindowToggles:
    """Per-window enable/disable toggles for _compute_binding."""

    _LONG_PATTERNS = ["seven_day", "sonnet", "opus"]

    def _windows(self, five_hour_util: float, seven_day_util: float) -> dict:
        from autoskillit.execution.quota import QuotaWindowEntry

        now = datetime.now(UTC)
        return {
            "five_hour": QuotaWindowEntry(
                utilization=five_hour_util, resets_at=now + timedelta(hours=3)
            ),
            "seven_day": QuotaWindowEntry(
                utilization=seven_day_util, resets_at=now + timedelta(days=4)
            ),
        }

    def test_compute_binding_defaults_both_enabled_preserves_behavior(self):
        """Test 1: default call (no toggle kwargs) — five_hour at 90% is binding and blocks."""
        from autoskillit.execution.quota import _compute_binding

        binding = _compute_binding(
            self._windows(90.0, 80.0),
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
        )
        assert binding.window_name == "five_hour"
        assert binding.should_block is True
        assert binding.effective_threshold == pytest.approx(85.0)

    def test_compute_binding_short_disabled_drops_five_hour(self):
        """Test 2: short_enabled=False — five_hour is dropped, seven_day survives."""
        from autoskillit.execution.quota import _compute_binding

        binding = _compute_binding(
            self._windows(90.0, 80.0),
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
            short_enabled=False,
            long_enabled=True,
        )
        assert binding.window_name == "seven_day"
        assert binding.should_block is False
        assert binding.effective_threshold == pytest.approx(98.0)

    def test_compute_binding_short_disabled_and_only_short_windows_present(self):
        """Test 3: all windows are short class → all dropped → empty sentinel."""
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        windows = {
            "one_hour": QuotaWindowEntry(utilization=95.0, resets_at=now + timedelta(minutes=45)),
            "five_hour": QuotaWindowEntry(utilization=90.0, resets_at=now + timedelta(hours=3)),
        }
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
            short_enabled=False,
        )
        assert binding.utilization == pytest.approx(0.0)
        assert binding.resets_at is None
        assert binding.should_block is False
        assert binding.effective_threshold == pytest.approx(100.0)
        assert binding.window_name == "unknown"

    def test_compute_binding_long_disabled_drops_weekly(self):
        """Test 4: long_enabled=False — weekly dropped, five_hour survives."""
        from autoskillit.execution.quota import _compute_binding

        binding = _compute_binding(
            self._windows(80.0, 99.0),
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
            long_enabled=False,
        )
        assert binding.window_name == "five_hour"
        assert binding.should_block is False
        assert binding.effective_threshold == pytest.approx(85.0)

    def test_compute_binding_long_disabled_suppresses_weekly_block(self):
        """Test 5: long_enabled=False — weekly at 99% is ignored, five_hour at 2% passes."""
        from autoskillit.execution.quota import _compute_binding

        binding = _compute_binding(
            self._windows(2.0, 99.0),
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
            long_enabled=False,
        )
        assert binding.window_name == "five_hour"
        assert binding.should_block is False

    def test_compute_binding_both_disabled_returns_sentinel(self):
        """Test 6: both disabled → empty sentinel with should_block=False."""
        from autoskillit.execution.quota import _compute_binding

        binding = _compute_binding(
            self._windows(90.0, 99.0),
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
            short_enabled=False,
            long_enabled=False,
        )
        assert binding.utilization == pytest.approx(0.0)
        assert binding.resets_at is None
        assert binding.should_block is False
        assert binding.effective_threshold == pytest.approx(100.0)

    def test_compute_binding_substring_classification_respects_long_patterns(self):
        """Test 7: weekly_sonnet is classified as long via substring match and dropped."""
        from autoskillit.execution.quota import QuotaWindowEntry, _compute_binding

        now = datetime.now(UTC)
        windows = {
            "weekly_sonnet": QuotaWindowEntry(utilization=99.0, resets_at=now + timedelta(days=6)),
            "five_hour": QuotaWindowEntry(utilization=90.0, resets_at=now + timedelta(hours=3)),
        }
        binding = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
            long_enabled=False,
        )
        assert binding.window_name == "five_hour"

    def test_compute_binding_both_enabled_matches_legacy_keyword_signature(self):
        """Test 8: explicit True values produce identical output to implicit defaults."""
        from autoskillit.execution.quota import _compute_binding

        windows = self._windows(90.0, 80.0)
        implicit = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
        )
        explicit = _compute_binding(
            windows,
            short_threshold=85.0,
            long_threshold=98.0,
            long_patterns=self._LONG_PATTERNS,
            short_enabled=True,
            long_enabled=True,
        )
        assert explicit.window_name == implicit.window_name
        assert explicit.should_block == implicit.should_block
        assert explicit.effective_threshold == pytest.approx(implicit.effective_threshold)
        assert explicit.utilization == pytest.approx(implicit.utilization)

    @pytest.mark.anyio
    async def test_check_and_sleep_respects_short_window_disabled(self, monkeypatch, tmp_path):
        """Test 9: short_window_enabled=False — five_hour at 90% is ignored."""
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        resets_at = datetime.now(UTC) + timedelta(hours=3)
        config = make_quota_guard_config(
            short_window_enabled=False,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        captured_kwargs: dict = {}

        async def fake_fetch(credentials_path, **kwargs):
            captured_kwargs.update(kwargs)
            return QuotaFetchResult(
                windows={
                    "five_hour": QuotaWindowEntry(utilization=90.0, resets_at=resets_at),
                    "weekly": QuotaWindowEntry(
                        utilization=80.0, resets_at=datetime.now(UTC) + timedelta(days=4)
                    ),
                },
                binding=QuotaStatus(
                    utilization=80.0,
                    resets_at=datetime.now(UTC) + timedelta(days=4),
                    window_name="weekly",
                    should_block=False,
                    effective_threshold=98.0,
                ),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert result["window_name"] == "weekly"
        assert captured_kwargs.get("short_enabled") is False

    @pytest.mark.anyio
    async def test_check_and_sleep_respects_long_window_disabled(self, monkeypatch, tmp_path):
        """Test 10: long_window_enabled=False — weekly at 99% is ignored."""
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        config = make_quota_guard_config(
            long_window_enabled=False,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        captured_kwargs: dict = {}

        async def fake_fetch(credentials_path, **kwargs):
            captured_kwargs.update(kwargs)
            return QuotaFetchResult(
                windows={
                    "weekly": QuotaWindowEntry(
                        utilization=99.0, resets_at=datetime.now(UTC) + timedelta(days=6)
                    ),
                    "five_hour": QuotaWindowEntry(
                        utilization=2.0, resets_at=datetime.now(UTC) + timedelta(hours=3)
                    ),
                },
                binding=QuotaStatus(
                    utilization=2.0,
                    resets_at=datetime.now(UTC) + timedelta(hours=3),
                    window_name="five_hour",
                    should_block=False,
                    effective_threshold=85.0,
                ),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert result["window_name"] == "five_hour"
        assert captured_kwargs.get("long_enabled") is False

    @pytest.mark.anyio
    async def test_check_and_sleep_both_disabled_returns_sentinel_no_sleep(
        self, monkeypatch, tmp_path
    ):
        """Test 11: both flags False → sentinel, no sleep, no error."""
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        config = make_quota_guard_config(
            short_window_enabled=False,
            long_window_enabled=False,
            credentials_path=str(tmp_path / ".credentials.json"),
            cache_path=str(tmp_path / "cache.json"),
        )

        async def fake_fetch(credentials_path, **kwargs):
            return QuotaFetchResult(
                windows={
                    "weekly": QuotaWindowEntry(
                        utilization=99.0, resets_at=datetime.now(UTC) + timedelta(days=6)
                    ),
                    "five_hour": QuotaWindowEntry(
                        utilization=99.0, resets_at=datetime.now(UTC) + timedelta(hours=3)
                    ),
                },
                binding=QuotaStatus(utilization=0.0, resets_at=None, effective_threshold=100.0),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert result["utilization"] == pytest.approx(0.0)
        assert result["resets_at"] is None
        assert "error" not in result

    @pytest.mark.anyio
    async def test_check_and_sleep_global_enabled_false_still_short_circuits(
        self, monkeypatch, tmp_path
    ):
        """Test 12: global enabled=False short-circuits before _fetch_quota is called."""
        fetch_called = []

        async def sentinel_fetch(*a, **kw):
            fetch_called.append(1)
            raise AssertionError("_fetch_quota must not be called when enabled=False")

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", sentinel_fetch)
        from autoskillit.execution.quota import check_and_sleep_if_needed

        config = make_quota_guard_config(
            enabled=False,
            short_window_enabled=True,
            long_window_enabled=True,
        )
        result = await check_and_sleep_if_needed(config)
        assert result["should_sleep"] is False
        assert fetch_called == []

    @pytest.mark.anyio
    async def test_refresh_quota_cache_forwards_per_window_toggles(self, monkeypatch, tmp_path):
        """Test 13: _refresh_quota_cache passes short_enabled/long_enabled to _fetch_quota."""
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _refresh_quota_cache,
        )

        captured_kwargs: dict = {}

        async def fake_fetch(credentials_path, **kwargs):
            captured_kwargs.update(kwargs)
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=0.1, resets_at=None)},
                binding=QuotaStatus(utilization=0.1, resets_at=None, window_name="five_hour"),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        config = make_quota_guard_config(
            short_window_enabled=False,
            cache_path=str(tmp_path / "cache.json"),
        )
        await _refresh_quota_cache(config)
        assert captured_kwargs["short_enabled"] is False
        assert captured_kwargs["long_enabled"] is True
