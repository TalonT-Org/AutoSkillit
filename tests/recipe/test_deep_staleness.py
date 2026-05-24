"""Tests for deep staleness detection in recipe/_api.py."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_deep_staleness_detects_rule_file_changes(tmp_path, monkeypatch):
    """Rule file changes trigger staleness even when pkg_root() mtime is unchanged."""
    import autoskillit.recipe._api as api_mod

    monkeypatch.setattr(api_mod, "_LOAD_CACHE", {})
    monkeypatch.setattr(api_mod, "_PROCESS_START_PKG_MTIME", 1000)
    monkeypatch.setattr(api_mod, "_STALENESS_LAST_CHECK", 0.0)
    monkeypatch.setattr(api_mod, "_STALENESS_IS_STALE", False)
    monkeypatch.setattr(api_mod, "_STALENESS_CACHES_CLEARED", False)
    monkeypatch.setattr(api_mod, "_DEEP_MTIME_BASELINE", 5000)

    real_mtime = api_mod._path_mtime_ns

    def fake_mtime(path):
        from autoskillit.core import pkg_root

        if path == pkg_root():
            return 1000
        return real_mtime(path)

    monkeypatch.setattr(api_mod, "_path_mtime_ns", fake_mtime)
    monkeypatch.setattr(api_mod, "_compute_deep_mtime", lambda: 9999)

    assert api_mod._check_process_staleness() is True


def test_deep_staleness_baseline_initialized_eagerly(tmp_path, monkeypatch):
    """_get_process_start_mtime eagerly sets the deep mtime baseline."""
    import autoskillit.recipe._api as api_mod

    monkeypatch.setattr(api_mod, "_PROCESS_START_PKG_MTIME", None)
    monkeypatch.setattr(api_mod, "_DEEP_MTIME_BASELINE", None)
    monkeypatch.setattr(api_mod, "_compute_deep_mtime", lambda: 5000)

    api_mod._get_process_start_mtime()
    assert api_mod._DEEP_MTIME_BASELINE == 5000


def test_staleness_check_skipped_for_fleet_sessions(monkeypatch):
    """Fleet sessions skip staleness — subprocess revalidates with fresh baselines."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    import autoskillit.recipe._api as api_mod

    # Force stale baselines
    monkeypatch.setattr(api_mod, "_PROCESS_START_PKG_MTIME", 1)
    monkeypatch.setattr(api_mod, "_DEEP_MTIME_BASELINE", 1)
    monkeypatch.setattr(api_mod, "_STALENESS_LAST_CHECK", 0.0)
    monkeypatch.setattr(api_mod, "_STALENESS_IS_STALE", False)

    assert api_mod._check_process_staleness() is False


def test_fleet_guard_still_initializes_baseline(monkeypatch):
    """FLEET guard must still call _get_process_start_mtime() so baseline is set."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    import autoskillit.recipe._api as api_mod

    monkeypatch.setattr(api_mod, "_PROCESS_START_PKG_MTIME", None)
    monkeypatch.setattr(api_mod, "_DEEP_MTIME_BASELINE", None)
    monkeypatch.setattr(api_mod, "_STALENESS_LAST_CHECK", 0.0)

    api_mod._check_process_staleness()

    assert api_mod._PROCESS_START_PKG_MTIME is not None
    assert api_mod._DEEP_MTIME_BASELINE is not None
