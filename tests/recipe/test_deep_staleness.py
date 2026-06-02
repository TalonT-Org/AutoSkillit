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


def test_yaml_only_change_invisible_to_staleness_detector(tmp_path, monkeypatch):
    """YAML-only change does NOT trigger _compute_content_hash (it only hashes *.py)."""
    import autoskillit.recipe._api_cache as cache_mod

    rule_dir = tmp_path / "recipe"
    rule_dir.mkdir()
    (rule_dir / "module_a.py").write_text("x = 1\n")
    (rule_dir / "contracts.yaml").write_text("version: 1\n")

    monkeypatch.setattr(cache_mod, "_STALENESS_SCAN_DIRS", ("recipe",))
    monkeypatch.setattr(cache_mod, "pkg_root", lambda: tmp_path)
    monkeypatch.setattr(cache_mod, "_PROCESS_START_PKG_MTIME", None)
    monkeypatch.setattr(cache_mod, "_DEEP_CONTENT_BASELINE", None)
    monkeypatch.setattr(cache_mod, "_STALENESS_LAST_CHECK", 0.0)
    monkeypatch.setattr(cache_mod, "_STALENESS_IS_STALE", False)

    cache_mod._get_process_start_mtime()
    baseline = cache_mod._DEEP_CONTENT_BASELINE

    (rule_dir / "contracts.yaml").write_text("version: 2\n")

    current = cache_mod._compute_content_hash()
    assert current == baseline, "YAML-only change should NOT change content hash (py-only)"


def test_yaml_file_cache_none_loader_result(tmp_path):
    """YamlFileCache correctly caches None loader results without re-calling."""
    from autoskillit.recipe._api_cache import YamlFileCache

    yaml_path = tmp_path / "empty.yaml"
    yaml_path.write_text("")

    call_count = 0

    def loader(path):
        nonlocal call_count
        call_count += 1
        return None

    cache = YamlFileCache()
    r1 = cache.get_or_load(yaml_path, loader)
    assert r1 is None
    assert call_count == 1

    r2 = cache.get_or_load(yaml_path, loader)
    assert r2 is None
    assert call_count == 1


def test_yaml_file_cache_invalidates_on_mtime_change(tmp_path):
    """YamlFileCache re-reads when file mtime changes."""
    import os

    from autoskillit.recipe._api_cache import YamlFileCache

    yaml_path = tmp_path / "data.yaml"
    yaml_path.write_text("v1")

    cache = YamlFileCache()
    r1 = cache.get_or_load(yaml_path, lambda p: p.read_text())
    assert r1 == "v1"

    yaml_path.write_text("v2-longer-content")
    os.utime(
        yaml_path,
        ns=(
            yaml_path.stat().st_atime_ns + 1_000_000_000,
            yaml_path.stat().st_mtime_ns + 1_000_000_000,
        ),
    )
    r2 = cache.get_or_load(yaml_path, lambda p: p.read_text())
    assert r2 == "v2-longer-content"


def test_manifest_mtime_change_forces_fresh_read(tmp_path, monkeypatch):
    """load_bundled_manifest re-reads when skill_contracts.yaml changes on disk."""
    from autoskillit.recipe._contracts_manifest import _MANIFEST_CACHE, load_bundled_manifest

    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    manifest_path = recipe_dir / "skill_contracts.yaml"
    manifest_path.write_text("skills:\n  old-skill:\n    inputs: []\n    outputs: []\n")

    monkeypatch.setattr("autoskillit.recipe._contracts_manifest.pkg_root", lambda: tmp_path)
    _MANIFEST_CACHE.clear()

    r1 = load_bundled_manifest()
    assert "old-skill" in r1.get("skills", {})

    manifest_path.write_text(
        "skills:\n  old-skill:\n    inputs: []\n    outputs: []\n"
        "  new-skill:\n    inputs: []\n    outputs: []\n"
    )

    r2 = load_bundled_manifest()
    assert "new-skill" in r2.get("skills", {}), "Manifest should reflect on-disk change"


def test_block_budgets_mtime_change_forces_fresh_read(tmp_path, monkeypatch):
    """_block_budgets re-reads when block_budgets.yaml changes on disk."""
    from autoskillit.recipe.rules.rules_blocks import _BUDGETS_CACHE, _block_budgets

    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    budgets_path = recipe_dir / "block_budgets.yaml"
    budgets_path.write_text("DEFAULT:\n  run_cmd: 5\n")

    monkeypatch.setattr("autoskillit.recipe.rules.rules_blocks.pkg_root", lambda: tmp_path)
    _BUDGETS_CACHE.clear()

    r1 = _block_budgets()
    assert r1.get("DEFAULT", {}).get("run_cmd") == 5

    budgets_path.write_text("DEFAULT:\n  run_cmd: 10\n")

    r2 = _block_budgets()
    assert r2.get("DEFAULT", {}).get("run_cmd") == 10


def test_block_budgets_missing_file_returns_empty_dict(tmp_path, monkeypatch):
    """_block_budgets returns {} when block_budgets.yaml does not exist."""
    from autoskillit.recipe.rules.rules_blocks import _BUDGETS_CACHE, _block_budgets

    monkeypatch.setattr("autoskillit.recipe.rules.rules_blocks.pkg_root", lambda: tmp_path)
    _BUDGETS_CACHE.clear()

    result = _block_budgets()
    assert result == {}


def test_ml_sub_area_folding_mtime_change_forces_fresh_read(tmp_path, monkeypatch):
    """load_ml_sub_area_folding re-reads when YAML changes on disk."""
    from autoskillit.recipe.methodology_venue_appendix import (
        _ML_SUB_AREA_CACHE,
        load_ml_sub_area_folding,
    )

    yaml_content_v1 = (
        "ml_sub_area_folding:\n"
        "  - sub_area: nlp\n"
        "    display_name: NLP\n"
        "    primary_parent: machine-learning\n"
        "    alternate_parents: []\n"
    )
    yaml_content_v2 = (
        "ml_sub_area_folding:\n"
        "  - sub_area: nlp\n"
        "    display_name: NLP\n"
        "    primary_parent: machine-learning\n"
        "    alternate_parents: []\n"
        "  - sub_area: cv\n"
        "    display_name: Computer Vision\n"
        "    primary_parent: machine-learning\n"
        "    alternate_parents: []\n"
    )
    yaml_path = tmp_path / "_ml_sub_area_folding.yaml"
    yaml_path.write_text(yaml_content_v1)

    monkeypatch.setattr(
        "autoskillit.recipe.methodology_venue_appendix.BUNDLED_METHODOLOGY_TRADITIONS_DIR",
        tmp_path,
    )
    _ML_SUB_AREA_CACHE.clear()

    r1 = load_ml_sub_area_folding()
    assert len(r1) == 1

    yaml_path.write_text(yaml_content_v2)

    r2 = load_ml_sub_area_folding()
    assert len(r2) == 2, "Should re-read and pick up the new entry"
