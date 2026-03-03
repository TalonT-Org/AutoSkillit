"""Tests for recipe/staleness_cache.py and cache-integrated check_contract_staleness."""

from __future__ import annotations

from autoskillit.recipe.staleness_cache import (
    StalenessEntry,
    compute_recipe_hash,
    read_staleness_cache,
    write_staleness_cache,
)


def _make_entry(**kwargs) -> StalenessEntry:
    defaults = dict(
        recipe_hash="sha256:" + "a" * 64,
        manifest_version="0.1.0",
        is_stale=False,
        triage_result=None,
        checked_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return StalenessEntry(**defaults)


# SC-1: missing cache file
def test_read_returns_none_when_cache_file_missing(tmp_path):
    assert read_staleness_cache(tmp_path / "c.json", "recipe") is None


# SC-2: recipe not yet cached
def test_read_returns_none_for_unknown_recipe(tmp_path):
    write_staleness_cache(tmp_path / "c.json", "other", _make_entry())
    assert read_staleness_cache(tmp_path / "c.json", "recipe") is None


# SC-3: returns entry regardless of hash/version mismatch (caller decides hit/miss)
def test_read_returns_entry_when_present(tmp_path):
    entry = _make_entry(recipe_hash="sha256:" + "a" * 64, manifest_version="0.1.0")
    write_staleness_cache(tmp_path / "c.json", "recipe", entry)
    result = read_staleness_cache(tmp_path / "c.json", "recipe")
    assert result is not None
    assert result.recipe_hash == "sha256:" + "a" * 64


# SC-4: write creates file; read roundtrips all fields
def test_write_read_roundtrip(tmp_path):
    cache_path = tmp_path / "c.json"
    entry = _make_entry(
        recipe_hash="sha256:" + "b" * 64,
        manifest_version="1.2.3",
        is_stale=True,
        triage_result="meaningful",
        checked_at="2026-03-01T09:30:00+00:00",
    )
    write_staleness_cache(cache_path, "my-recipe", entry)
    assert cache_path.is_file()
    result = read_staleness_cache(cache_path, "my-recipe")
    assert result is not None
    assert result.recipe_hash == "sha256:" + "b" * 64
    assert result.manifest_version == "1.2.3"
    assert result.is_stale is True
    assert result.triage_result == "meaningful"
    assert result.checked_at == "2026-03-01T09:30:00+00:00"


# SC-5: write updates an existing entry in-place (other entries preserved)
def test_write_updates_existing_entry(tmp_path):
    cache_path = tmp_path / "c.json"
    entry_a = _make_entry(recipe_hash="sha256:" + "a" * 64)
    entry_b = _make_entry(recipe_hash="sha256:" + "b" * 64, manifest_version="2.0.0")
    write_staleness_cache(cache_path, "recipe-a", entry_a)
    write_staleness_cache(cache_path, "recipe-b", entry_b)

    # Update recipe-a only
    updated = _make_entry(recipe_hash="sha256:" + "c" * 64, is_stale=True)
    write_staleness_cache(cache_path, "recipe-a", updated)

    result_a = read_staleness_cache(cache_path, "recipe-a")
    result_b = read_staleness_cache(cache_path, "recipe-b")

    assert result_a is not None
    assert result_a.recipe_hash == "sha256:" + "c" * 64
    assert result_a.is_stale is True

    # recipe-b must be preserved
    assert result_b is not None
    assert result_b.recipe_hash == "sha256:" + "b" * 64
    assert result_b.manifest_version == "2.0.0"


# SC-6: compute_recipe_hash returns "sha256:" + 64-char hex string
def test_compute_recipe_hash_format(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text("name: x")
    h = compute_recipe_hash(f)
    assert h.startswith("sha256:") and len(h) == 71


# SC-7: check_contract_staleness returns [] without calling compute_skill_hash
#       when cache hit and is_stale=False
def test_check_staleness_fast_path_on_not_stale_cache_hit(monkeypatch, tmp_path):
    from autoskillit.recipe.contracts import check_contract_staleness, load_bundled_manifest

    recipe_file = tmp_path / "r.yaml"
    recipe_file.write_bytes(b"name: x")

    manifest_version = load_bundled_manifest()["version"]

    entry = _make_entry(
        recipe_hash=compute_recipe_hash(recipe_file),
        manifest_version=manifest_version,
        is_stale=False,
    )
    write_staleness_cache(tmp_path / "c.json", "r", entry)

    def _raise(*a):
        raise AssertionError("should not read SKILL.md")

    monkeypatch.setattr("autoskillit.recipe.contracts.compute_skill_hash", _raise)

    contract = {
        "bundled_manifest_version": manifest_version,
        "skill_hashes": {"make-plan": "sha256:x"},
    }
    result = check_contract_staleness(
        contract,
        recipe_path=recipe_file,
        cache_path=tmp_path / "c.json",
    )
    assert result == []


# SC-8: cache miss causes compute_skill_hash to be called and cache to be written
def test_check_staleness_writes_cache_on_miss(monkeypatch, tmp_path):
    from autoskillit.recipe.contracts import check_contract_staleness, load_bundled_manifest

    recipe_file = tmp_path / "r.yaml"
    recipe_file.write_bytes(b"name: x")
    cache_path = tmp_path / "c.json"

    manifest_version = load_bundled_manifest()["version"]

    compute_called: list[str] = []

    def tracking_compute(skill_name: str, *, skills_dir) -> str:
        compute_called.append(skill_name)
        return "sha256:" + "b" * 64

    monkeypatch.setattr("autoskillit.recipe.contracts.compute_skill_hash", tracking_compute)

    contract = {
        "bundled_manifest_version": manifest_version,
        "skill_hashes": {"make-plan": "sha256:" + "a" * 64},
    }
    assert not cache_path.is_file()

    result = check_contract_staleness(
        contract,
        recipe_path=recipe_file,
        cache_path=cache_path,
        skills_dir=tmp_path,
    )

    assert "make-plan" in compute_called
    assert cache_path.is_file()
    cached = read_staleness_cache(cache_path, "r")
    assert cached is not None
    assert cached.is_stale is True
    assert cached.triage_result is None
    assert len(result) == 1
    assert result[0].skill == "make-plan"


# SC-9: cache hit with is_stale=True falls through to full check for StaleItem details
def test_check_staleness_stale_hit_still_returns_items(monkeypatch, tmp_path):
    from autoskillit.recipe.contracts import check_contract_staleness, load_bundled_manifest

    recipe_file = tmp_path / "r.yaml"
    recipe_file.write_bytes(b"name: x")
    cache_path = tmp_path / "c.json"

    manifest_version = load_bundled_manifest()["version"]

    entry = _make_entry(
        recipe_hash=compute_recipe_hash(recipe_file),
        manifest_version=manifest_version,
        is_stale=True,
        triage_result=None,
    )
    write_staleness_cache(cache_path, "r", entry)

    monkeypatch.setattr(
        "autoskillit.recipe.contracts.compute_skill_hash",
        lambda skill_name, *, skills_dir: "sha256:" + "b" * 64,
    )

    contract = {
        "bundled_manifest_version": manifest_version,
        "skill_hashes": {"make-plan": "sha256:" + "a" * 64},
    }

    result = check_contract_staleness(
        contract,
        recipe_path=recipe_file,
        cache_path=cache_path,
        skills_dir=tmp_path,
    )

    # Despite cache hit with is_stale=True, should return StaleItem details
    assert len(result) == 1
    assert result[0].skill == "make-plan"
    assert result[0].reason == "hash_mismatch"
