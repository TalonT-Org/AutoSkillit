from __future__ import annotations

import re
from pathlib import Path

from autoskillit.sync_manifest import (
    SyncDecisionStore,
    SyncManifest,
    compute_recipe_hash,
)

CONTENT_A = "name: recipe-a\ndescription: first\n"
CONTENT_B = "name: recipe-b\ndescription: different\n"


class TestComputeRecipeHash:
    def test_compute_recipe_hash_format(self) -> None:
        """SM1: Result is sha256:<64-hex-chars>"""
        result = compute_recipe_hash(CONTENT_A)
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", result)

    def test_compute_recipe_hash_deterministic(self) -> None:
        """SM2: Same content → same hash on repeated calls"""
        assert compute_recipe_hash(CONTENT_A) == compute_recipe_hash(CONTENT_A)

    def test_compute_recipe_hash_differs_for_different_content(self) -> None:
        """SM3: Different content → different hash"""
        assert compute_recipe_hash(CONTENT_A) != compute_recipe_hash(CONTENT_B)


class TestSyncManifest:
    def test_sync_manifest_empty_on_first_load(self, tmp_path: Path) -> None:
        """SM4: New SyncManifest at non-existent path returns {} from load()"""
        manifest = SyncManifest(tmp_path / "nonexistent" / "manifest.json")
        assert manifest.load() == {}

    def test_sync_manifest_record_stores_hash(self, tmp_path: Path) -> None:
        """SM5: After record(), get_hash() equals compute_recipe_hash(content)"""
        manifest = SyncManifest(tmp_path / "manifest.json")
        manifest.record("my-recipe", CONTENT_A)
        assert manifest.get_hash("my-recipe") == compute_recipe_hash(CONTENT_A)

    def test_sync_manifest_get_hash_returns_none_for_unknown(self, tmp_path: Path) -> None:
        """SM6: get_hash() returns None for unknown recipe"""
        manifest = SyncManifest(tmp_path / "manifest.json")
        assert manifest.get_hash("nonexistent") is None

    def test_sync_manifest_persists_across_instances(self, tmp_path: Path) -> None:
        """SM7: Write with one SyncManifest(path), read with SyncManifest(path) at same path"""
        store_path = tmp_path / "manifest.json"
        SyncManifest(store_path).record("recipe-x", CONTENT_A)
        result = SyncManifest(store_path).get_hash("recipe-x")
        assert result == compute_recipe_hash(CONTENT_A)

    def test_sync_manifest_creates_parent_dirs(self, tmp_path: Path) -> None:
        """SM8: SyncManifest at path with non-existent parent writes successfully"""
        store_path = tmp_path / "deep" / "nested" / "manifest.json"
        manifest = SyncManifest(store_path)
        manifest.record("recipe-y", CONTENT_B)
        assert store_path.exists()
        assert manifest.get_hash("recipe-y") == compute_recipe_hash(CONTENT_B)


class TestSyncDecisionStore:
    def test_sync_decision_store_is_declined_false_for_unknown(self, tmp_path: Path) -> None:
        """SM9: is_declined() is False on empty store"""
        store = SyncDecisionStore(tmp_path / "decisions.json")
        assert store.is_declined("recipe", "sha256:abc") is False

    def test_sync_decision_record_decline_makes_is_declined_true(self, tmp_path: Path) -> None:
        """SM10: After record_decline(), is_declined() is True"""
        store = SyncDecisionStore(tmp_path / "decisions.json")
        store.record_decline("recipe", "sha256:abc")
        assert store.is_declined("recipe", "sha256:abc") is True

    def test_sync_decision_decline_scoped_to_exact_hash(self, tmp_path: Path) -> None:
        """SM11: Declined at hash H → is_declined() is False for different hash"""
        store = SyncDecisionStore(tmp_path / "decisions.json")
        store.record_decline("recipe", "sha256:abc")
        assert store.is_declined("recipe", "sha256:xyz") is False

    def test_sync_decision_record_accept_does_not_set_declined(self, tmp_path: Path) -> None:
        """SM12: After record_accept(), is_declined() remains False"""
        store = SyncDecisionStore(tmp_path / "decisions.json")
        store.record_accept("recipe", "sha256:abc")
        assert store.is_declined("recipe", "sha256:abc") is False

    def test_sync_decision_store_persists_across_instances(self, tmp_path: Path) -> None:
        """SM13: Write with one instance, read with new instance at same path"""
        store_path = tmp_path / "decisions.json"
        SyncDecisionStore(store_path).record_decline("recipe-z", "sha256:def")
        assert SyncDecisionStore(store_path).is_declined("recipe-z", "sha256:def") is True
