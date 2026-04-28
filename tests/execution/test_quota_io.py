"""Tests for execution/quota.py — credential reading, cache I/O, dataclass validation, and cache schema versioning."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tests._helpers import make_quota_guard_config

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


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
            "schema_version": 3,
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
                "should_block": True,
                "effective_threshold": 85.0,
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

    def test_read_cache_null_utilization_returns_none(self, tmp_path):
        """Cache with utilization: null in binding must return None (fail-open)."""
        from autoskillit.execution.quota import _read_cache

        cache_path = tmp_path / "usage_cache.json"
        payload = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "binding": {"utilization": None, "resets_at": None, "window_name": "five_hour"},
        }
        cache_path.write_text(json.dumps(payload))
        assert _read_cache(str(cache_path), max_age=300) is None

    def test_read_cache_typeerror_returns_none(self, tmp_path):
        """_read_cache must catch TypeError (e.g., float(None)) and return None."""
        from autoskillit.execution.quota import _read_cache

        cache_path = tmp_path / "usage_cache.json"
        payload = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "binding": {"utilization": None, "resets_at": None},
        }
        cache_path.write_text(json.dumps(payload))
        # With __post_init__, constructing QuotaStatus with None raises TypeError.
        # _read_cache must catch it and return None.
        assert _read_cache(str(cache_path), max_age=300) is None


class TestQuotaDataclassPostInit:
    """QuotaStatus and QuotaWindowEntry must enforce types at construction."""

    def test_quota_window_entry_rejects_none_utilization(self):
        from autoskillit.execution.quota import QuotaWindowEntry

        with pytest.raises(TypeError, match="utilization"):
            QuotaWindowEntry(utilization=None, resets_at=None)

    def test_quota_status_rejects_none_utilization(self):
        from autoskillit.execution.quota import QuotaStatus

        with pytest.raises(TypeError, match="utilization"):
            QuotaStatus(utilization=None, resets_at=None)

    def test_quota_window_entry_coerces_string_utilization(self):
        from autoskillit.execution.quota import QuotaWindowEntry

        entry = QuotaWindowEntry(utilization="85.5", resets_at=None)
        assert entry.utilization == 85.5
        assert isinstance(entry.utilization, float)

    def test_quota_status_coerces_string_utilization(self):
        from autoskillit.execution.quota import QuotaStatus

        status = QuotaStatus(utilization="42.0", resets_at=None)
        assert status.utilization == 42.0
        assert isinstance(status.utilization, float)


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


class TestCacheSchemaVersion:
    """Phase 4 (#711 Part B): quota cache schema versioning tests."""

    def setup_method(self):
        from autoskillit.execution.quota import _reset_schema_drift_logged_for_tests

        _reset_schema_drift_logged_for_tests()

    def test_write_cache_embeds_schema_version(self, tmp_path):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _write_cache,
        )

        result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=50.0, resets_at=None)},
            binding=QuotaStatus(utilization=50.0, resets_at=None, window_name="five_hour"),
        )
        cache_path = tmp_path / "cache.json"
        _write_cache(str(cache_path), result)
        raw = json.loads(cache_path.read_text())
        assert raw["schema_version"] == 3

    def test_write_cache_uses_write_versioned_json(self, tmp_path, monkeypatch):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _write_cache,
        )

        calls = []

        def spy(path, payload, schema_version):
            calls.append({"path": path, "schema_version": schema_version})
            # Call through to real impl
            from autoskillit.core import write_versioned_json as real

            real(path, payload, schema_version)

        monkeypatch.setattr("autoskillit.execution.quota.write_versioned_json", spy)

        result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=50.0, resets_at=None)},
            binding=QuotaStatus(utilization=50.0, resets_at=None, window_name="five_hour"),
        )
        cache_path = tmp_path / "cache.json"
        _write_cache(str(cache_path), result)
        assert len(calls) == 1
        assert calls[0]["schema_version"] == 3

    def test_read_cache_schema_round_trip_returns_status(self, tmp_path):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            _read_cache,
            _write_cache,
        )

        resets_at = datetime(2026, 2, 27, 20, 15, tzinfo=UTC)
        result = QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=60.0, resets_at=resets_at)},
            binding=QuotaStatus(utilization=60.0, resets_at=resets_at, window_name="five_hour"),
        )
        cache_path = tmp_path / "cache.json"
        _write_cache(str(cache_path), result)
        status = _read_cache(str(cache_path), max_age=60)
        assert status is not None
        assert status.utilization == pytest.approx(60.0)

    def test_read_cache_missing_schema_version_returns_none(self, tmp_path):
        from autoskillit.execution.quota import _read_cache

        cache_path = tmp_path / "cache.json"
        old_data = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "windows": {},
            "binding": {"utilization": 50.0, "resets_at": None, "window_name": "five_hour"},
        }
        cache_path.write_text(json.dumps(old_data))
        assert _read_cache(str(cache_path), max_age=60) is None

    def test_read_cache_wrong_schema_version_returns_none(self, tmp_path):
        from autoskillit.execution.quota import _read_cache

        cache_path = tmp_path / "cache.json"
        old_data = {
            "schema_version": 1,
            "fetched_at": datetime.now(UTC).isoformat(),
            "windows": {},
            "binding": {"utilization": 50.0, "resets_at": None, "window_name": "five_hour"},
        }
        cache_path.write_text(json.dumps(old_data))
        assert _read_cache(str(cache_path), max_age=60) is None

    def test_read_cache_logs_drift_warning_on_schema_mismatch(self, tmp_path):
        import structlog.testing

        from autoskillit.execution.quota import _read_cache

        cache_path = tmp_path / "cache.json"
        old_data = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "windows": {},
            "binding": {"utilization": 50.0, "resets_at": None, "window_name": "five_hour"},
        }
        cache_path.write_text(json.dumps(old_data))

        with structlog.testing.capture_logs() as cap:
            _read_cache(str(cache_path), max_age=60)

        drift_logs = [r for r in cap if "quota_cache_schema_drift" in r.get("event", "")]
        assert len(drift_logs) == 1
        assert "cache_path" in drift_logs[0]
        assert drift_logs[0]["observed"] is None

    def test_read_cache_logs_drift_exactly_once_per_path_per_process(self, tmp_path):
        import structlog.testing

        from autoskillit.execution.quota import _read_cache

        cache_path = tmp_path / "cache.json"
        old_data = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "windows": {},
            "binding": {"utilization": 50.0, "resets_at": None, "window_name": "five_hour"},
        }
        cache_path.write_text(json.dumps(old_data))

        with structlog.testing.capture_logs() as cap:
            for _ in range(5):
                _read_cache(str(cache_path), max_age=60)

        drift_logs = [r for r in cap if "quota_cache_schema_drift" in r.get("event", "")]
        assert len(drift_logs) == 1

    def test_read_cache_logs_drift_once_per_path_not_globally(self, tmp_path):
        import structlog.testing

        from autoskillit.execution.quota import _read_cache

        for name in ("cache_a.json", "cache_b.json"):
            path = tmp_path / name
            path.write_text(
                json.dumps(
                    {
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "windows": {},
                        "binding": {"utilization": 50.0},
                    }
                )
            )

        with structlog.testing.capture_logs() as cap:
            _read_cache(str(tmp_path / "cache_a.json"), max_age=60)
            _read_cache(str(tmp_path / "cache_b.json"), max_age=60)

        drift_logs = [r for r in cap if "quota_cache_schema_drift" in r.get("event", "")]
        assert len(drift_logs) == 2

    def test_read_cache_drift_set_is_module_scoped_and_reset_helper_works(self, tmp_path):
        import structlog.testing

        from autoskillit.execution.quota import (
            _read_cache,
            _reset_schema_drift_logged_for_tests,
        )

        cache_path = tmp_path / "cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "windows": {},
                    "binding": {"utilization": 50.0},
                }
            )
        )

        with structlog.testing.capture_logs() as cap1:
            _read_cache(str(cache_path), max_age=60)
        assert len([r for r in cap1 if "quota_cache_schema_drift" in r.get("event", "")]) == 1

        _reset_schema_drift_logged_for_tests()

        with structlog.testing.capture_logs() as cap2:
            _read_cache(str(cache_path), max_age=60)
        assert len([r for r in cap2 if "quota_cache_schema_drift" in r.get("event", "")]) == 1

    @pytest.mark.anyio
    async def test_old_format_cache_gets_rewritten_with_new_format_on_next_fetch(
        self, tmp_path, monkeypatch
    ):
        from autoskillit.execution.quota import (
            QuotaFetchResult,
            QuotaStatus,
            QuotaWindowEntry,
            check_and_sleep_if_needed,
        )

        cache_path = tmp_path / "cache.json"
        # Write old-format cache (no schema_version)
        old_data = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "windows": {},
            "binding": {"utilization": 50.0, "resets_at": None, "window_name": "five_hour"},
        }
        cache_path.write_text(json.dumps(old_data))

        async def fake_fetch(credentials_path, **kwargs):
            return QuotaFetchResult(
                windows={"five_hour": QuotaWindowEntry(utilization=30.0, resets_at=None)},
                binding=QuotaStatus(utilization=30.0, resets_at=None, window_name="five_hour"),
            )

        monkeypatch.setattr("autoskillit.execution.quota._fetch_quota", fake_fetch)
        config = make_quota_guard_config(
            cache_path=str(cache_path),
            credentials_path=str(tmp_path / "fake_creds.json"),
        )
        await check_and_sleep_if_needed(config)
        new_data = json.loads(cache_path.read_text())
        assert new_data["schema_version"] == 3


class TestInvalidateCache:
    def test_invalidate_cache_removes_existing_file(self, tmp_path):
        from autoskillit.execution.quota import invalidate_cache

        cache_file = tmp_path / "quota_cache.json"
        cache_file.write_text('{"version": 3}')
        assert cache_file.exists()

        invalidate_cache(str(cache_file))

        assert not cache_file.exists()

    def test_invalidate_cache_tolerates_missing_file(self, tmp_path):
        from autoskillit.execution.quota import invalidate_cache

        missing_path = tmp_path / "nonexistent_cache.json"
        invalidate_cache(str(missing_path))  # must not raise

    def test_invalidate_cache_logs_warning_on_permission_error(self, monkeypatch):
        import structlog.testing

        from autoskillit.execution.quota import invalidate_cache

        def _raise_permission_error(self, missing_ok=False):
            raise PermissionError("denied")

        from pathlib import Path

        monkeypatch.setattr(Path, "unlink", _raise_permission_error)

        with structlog.testing.capture_logs() as cap:
            invalidate_cache("/some/path/cache.json")

        assert any("quota cache invalidation failed" in rec.get("event", "") for rec in cap)

    def test_invalidate_cache_expands_user_tilde(self, monkeypatch):
        from pathlib import Path

        from autoskillit.execution.quota import invalidate_cache

        unlinked_paths: list[str] = []

        def _capture_unlink(self, missing_ok=False):
            unlinked_paths.append(str(self))

        monkeypatch.setattr(Path, "unlink", _capture_unlink)

        invalidate_cache("~/some/cache.json")

        assert len(unlinked_paths) == 1
        assert not unlinked_paths[0].startswith("~")
        assert "cache.json" in unlinked_paths[0]


class TestAPIWindowVocabularyContract:
    """Contract tests: LONG_WINDOW_NAMES × default long_window_patterns → correct classification.

    These tests bind the vocabulary constants in quota.py to the config defaults in settings.py.
    Any change to either that breaks this invariant will fail here immediately.
    """

    def test_long_window_names_all_match_default_patterns(self):
        """Every name in LONG_WINDOW_NAMES must be classified as long by the default patterns."""
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import LONG_WINDOW_NAMES, _is_long_window

        defaults = QuotaGuardConfig().long_window_patterns
        for name in sorted(LONG_WINDOW_NAMES):
            assert _is_long_window(name, defaults), (
                f"Known long window {name!r} not matched by default patterns {defaults!r}. "
                "Either add a matching pattern to long_window_patterns defaults, "
                "or remove the name from LONG_WINDOW_NAMES if it is no longer a long window."
            )

    def test_known_short_windows_not_classified_as_long_by_default(self):
        """Windows not in LONG_WINDOW_NAMES must not match long patterns."""
        from autoskillit.config.settings import QuotaGuardConfig
        from autoskillit.execution.quota import (
            KNOWN_QUOTA_WINDOW_NAMES,
            LONG_WINDOW_NAMES,
            _is_long_window,
        )

        defaults = QuotaGuardConfig().long_window_patterns
        pure_short = KNOWN_QUOTA_WINDOW_NAMES - LONG_WINDOW_NAMES
        for name in sorted(pure_short):
            assert not _is_long_window(name, defaults), (
                f"Known short window {name!r} is being classified as long "
                f"by patterns {defaults!r}. "
                "This would apply the long threshold to a short window."
            )

    def test_long_window_names_constant_exists_and_is_nonempty(self):
        """LONG_WINDOW_NAMES must be a non-empty frozenset exported from quota.py."""
        from autoskillit.execution import quota

        assert hasattr(quota, "LONG_WINDOW_NAMES")
        assert isinstance(quota.LONG_WINDOW_NAMES, frozenset)
        assert len(quota.LONG_WINDOW_NAMES) > 0

    def test_known_quota_window_names_constant_exists_and_contains_long_names(self):
        """KNOWN_QUOTA_WINDOW_NAMES must include all entries from LONG_WINDOW_NAMES."""
        from autoskillit.execution.quota import KNOWN_QUOTA_WINDOW_NAMES, LONG_WINDOW_NAMES

        assert LONG_WINDOW_NAMES.issubset(KNOWN_QUOTA_WINDOW_NAMES), (
            f"LONG_WINDOW_NAMES contains names not in KNOWN_QUOTA_WINDOW_NAMES: "
            f"{LONG_WINDOW_NAMES - KNOWN_QUOTA_WINDOW_NAMES}"
        )
