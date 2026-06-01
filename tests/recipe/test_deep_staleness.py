"""Tests for deep staleness detection in recipe/_api_cache.py."""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_deep_staleness_detects_rule_file_changes(tmp_path, monkeypatch):
    """Rule file content changes trigger staleness."""
    import autoskillit.recipe._api as api_mod
    import autoskillit.recipe._api_cache as cache_mod

    monkeypatch.setattr(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache())
    monkeypatch.setattr(cache_mod, "_PROCESS_START_PKG_MTIME", 1000)
    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    monkeypatch.setattr(cache_mod, "_STALENESS_IS_STALE", False)
    monkeypatch.setattr(cache_mod, "_STALENESS_CACHES_CLEARED", False)
    monkeypatch.setattr(cache_mod, "_DEEP_CONTENT_BASELINE", "fakehash_baseline")
    monkeypatch.setattr(cache_mod, "_compute_content_hash", lambda: "fakehash_changed")

    assert api_mod._check_process_staleness() is True


def test_deep_staleness_baseline_initialized_eagerly(tmp_path, monkeypatch):
    """_get_process_start_mtime eagerly sets the content hash baseline."""
    import autoskillit.recipe._api_cache as cache_mod

    monkeypatch.setattr(cache_mod, "_PROCESS_START_PKG_MTIME", None)
    monkeypatch.setattr(cache_mod, "_DEEP_CONTENT_BASELINE", None)
    monkeypatch.setattr(cache_mod, "_compute_content_hash", lambda: "fakehash_init")

    cache_mod._get_process_start_mtime()
    assert cache_mod._DEEP_CONTENT_BASELINE == "fakehash_init"


def test_staleness_check_skipped_for_fleet_sessions(monkeypatch):
    """Fleet sessions skip staleness — subprocess revalidates with fresh baselines."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    import autoskillit.recipe._api as api_mod
    import autoskillit.recipe._api_cache as cache_mod

    monkeypatch.setattr(cache_mod, "_PROCESS_START_PKG_MTIME", 1)
    monkeypatch.setattr(cache_mod, "_DEEP_CONTENT_BASELINE", "fakehash_a")
    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    monkeypatch.setattr(cache_mod, "_STALENESS_IS_STALE", False)

    assert api_mod._check_process_staleness() is False


def test_fleet_guard_still_initializes_baseline(monkeypatch):
    """FLEET guard must still call _get_process_start_mtime() so baseline is set."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    import autoskillit.recipe._api as api_mod
    import autoskillit.recipe._api_cache as cache_mod

    monkeypatch.setattr(cache_mod, "_PROCESS_START_PKG_MTIME", None)
    monkeypatch.setattr(cache_mod, "_DEEP_CONTENT_BASELINE", None)
    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)

    api_mod._check_process_staleness()

    assert cache_mod._PROCESS_START_PKG_MTIME is not None
    assert cache_mod._DEEP_CONTENT_BASELINE is not None


def test_content_hash_ignores_mtime_only_change(tmp_path, monkeypatch):
    """mtime change with identical content must NOT trigger staleness."""
    import autoskillit.recipe._api_cache as cache_mod

    rule_dir = tmp_path / "recipe"
    rule_dir.mkdir()
    (rule_dir / "module_a.py").write_text("x = 1\n")
    (rule_dir / "module_b.py").write_text("y = 2\n")

    monkeypatch.setattr(cache_mod, "_STALENESS_SCAN_DIRS", ("recipe",))
    monkeypatch.setattr(cache_mod, "pkg_root", lambda: tmp_path)
    monkeypatch.setattr(cache_mod, "_PROCESS_START_PKG_MTIME", None)
    monkeypatch.setattr(cache_mod, "_DEEP_CONTENT_BASELINE", None)
    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    monkeypatch.setattr(cache_mod, "_STALENESS_IS_STALE", False)

    cache_mod._get_process_start_mtime()
    baseline = cache_mod._DEEP_CONTENT_BASELINE

    for f in rule_dir.glob("*.py"):
        st = f.stat()
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))

    current = cache_mod._compute_content_hash()
    assert current == baseline

    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    assert cache_mod._check_process_staleness() is False


def test_content_hash_detects_real_code_change(tmp_path, monkeypatch):
    """Byte-level change to a rule file must trigger staleness."""
    import autoskillit.recipe._api_cache as cache_mod

    rule_dir = tmp_path / "recipe"
    rule_dir.mkdir()
    (rule_dir / "module_a.py").write_text("x = 1\n")

    monkeypatch.setattr(cache_mod, "_STALENESS_SCAN_DIRS", ("recipe",))
    monkeypatch.setattr(cache_mod, "pkg_root", lambda: tmp_path)
    monkeypatch.setattr(cache_mod, "_PROCESS_START_PKG_MTIME", None)
    monkeypatch.setattr(cache_mod, "_DEEP_CONTENT_BASELINE", None)
    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    monkeypatch.setattr(cache_mod, "_STALENESS_IS_STALE", False)

    cache_mod._get_process_start_mtime()

    (rule_dir / "module_a.py").write_text("x = 999\n")

    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    assert cache_mod._check_process_staleness() is True


def test_baseline_refresh_after_successful_load(tmp_path, monkeypatch):
    """After _refresh_staleness_baseline, subsequent checks return non-stale."""
    import autoskillit.recipe._api_cache as cache_mod

    rule_dir = tmp_path / "recipe"
    rule_dir.mkdir()
    (rule_dir / "module_a.py").write_text("x = 1\n")

    monkeypatch.setattr(cache_mod, "_STALENESS_SCAN_DIRS", ("recipe",))
    monkeypatch.setattr(cache_mod, "pkg_root", lambda: tmp_path)
    monkeypatch.setattr(cache_mod, "_PROCESS_START_PKG_MTIME", None)
    monkeypatch.setattr(cache_mod, "_DEEP_CONTENT_BASELINE", None)
    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    monkeypatch.setattr(cache_mod, "_STALENESS_IS_STALE", False)

    cache_mod._get_process_start_mtime()

    (rule_dir / "module_a.py").write_text("x = 999\n")
    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    assert cache_mod._check_process_staleness() is True

    cache_mod._refresh_staleness_baseline()

    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    assert cache_mod._check_process_staleness() is False


def test_registry_hash_content_based(tmp_path):
    """Registry hash must be identical for same-content files regardless of mtime."""
    from autoskillit.recipe._api_cache import _compute_registry_hash

    d = tmp_path / "types"
    d.mkdir()
    f = d / "test.yaml"
    f.write_text("name: test\n")
    h1 = _compute_registry_hash(d)

    f.write_text("name: test\n")
    stat = f.stat()
    os.utime(f, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    h2 = _compute_registry_hash(d)

    assert h1 == h2

    f.write_text("name: changed\n")
    h3 = _compute_registry_hash(d)
    assert h1 != h3


def test_staleness_scan_covers_recipe_top_level(monkeypatch):
    """_STALENESS_SCAN_DIRS must include recipe/ root, not just recipe/rules/."""
    import autoskillit.recipe._api_cache as cache_mod

    assert "recipe" in cache_mod._STALENESS_SCAN_DIRS
