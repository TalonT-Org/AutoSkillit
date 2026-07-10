"""Tests for the IL-0 recipe compilation typed transport (Step 2.2/2.5 of #4185)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    KitchenInstanceId,
    RecipeCompilationFailure,
    RecipeCompilationKey,
    RecipeCompilationResult,
    SkillSource,
    compute_compilation_key_fingerprint,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _make_key(**overrides: object) -> RecipeCompilationKey:
    defaults: dict[str, object] = {
        "project_dir": Path("/tmp"),
        "recipe_name": "demo",
        "raw_content_identity": "abc",
        "composite_content_identity": "def",
        "sub_recipe_content_identities": (),
        "selected_skill_identities": (),
        "resolved_defaults": {},
        "caller_overrides": {},
        "session_overrides": {},
        "suppressed_rules": frozenset(),
        "defer_unresolved": False,
        "backend_name": None,
        "effective_backend_map": {},
        "bundled_contract_version": "v1",
        "project_contract_identity": None,
        "tool_registry_hash": "hash1",
        "rule_view_registry_hash": "hash2",
        "kitchen_instance_id": None,
        "feature_inputs": {},
    }
    defaults.update(overrides)
    return RecipeCompilationKey(**defaults)  # type: ignore[arg-type]


class TestKitchenInstanceId:
    def test_valid_construction(self) -> None:
        kiid = KitchenInstanceId(value="abc", process_id=42)
        assert kiid.value == "abc"
        assert kiid.process_id == 42

    def test_empty_value_raises(self) -> None:
        with pytest.raises(ValueError, match="value must be non-empty"):
            KitchenInstanceId(value="", process_id=1)

    def test_zero_process_id_raises(self) -> None:
        with pytest.raises(ValueError, match="process_id must be positive"):
            KitchenInstanceId(value="x", process_id=0)

    def test_negative_process_id_raises(self) -> None:
        with pytest.raises(ValueError, match="process_id must be positive"):
            KitchenInstanceId(value="x", process_id=-1)

    def test_is_hashable(self) -> None:
        kiid = KitchenInstanceId(value="abc", process_id=42)
        assert hash(kiid) == hash(kiid)


class TestRecipeCompilationKey:
    def test_construction_with_minimum_fields(self) -> None:
        key = _make_key()
        assert key.recipe_name == "demo"
        assert key.fingerprint != ""

    def test_empty_recipe_name_raises(self) -> None:
        with pytest.raises(ValueError, match="recipe_name must be non-empty"):
            _make_key(recipe_name="")

    def test_empty_raw_content_identity_raises(self) -> None:
        with pytest.raises(ValueError, match="raw_content_identity must be non-empty"):
            _make_key(raw_content_identity="")

    def test_empty_tool_registry_hash_raises(self) -> None:
        with pytest.raises(ValueError, match="tool_registry_hash must be non-empty"):
            _make_key(tool_registry_hash="")

    def test_empty_rule_view_registry_hash_raises(self) -> None:
        with pytest.raises(ValueError, match="rule_view_registry_hash must be non-empty"):
            _make_key(rule_view_registry_hash="")

    def test_key_with_kitchen_instance_id(self) -> None:
        kiid = KitchenInstanceId(value="kitchen-1", process_id=99)
        key = _make_key(kitchen_instance_id=kiid)
        assert key.kitchen_instance_id is kiid

    def test_identical_keys_have_identical_fingerprints(self) -> None:
        a = _make_key()
        b = _make_key()
        assert a.fingerprint == b.fingerprint

    def test_different_raw_content_changes_fingerprint(self) -> None:
        a = _make_key()
        b = _make_key(raw_content_identity="different")
        assert a.fingerprint != b.fingerprint

    def test_different_tool_registry_hash_changes_fingerprint(self) -> None:
        a = _make_key()
        b = _make_key(tool_registry_hash="hash-X")
        assert a.fingerprint != b.fingerprint

    def test_different_backend_name_changes_fingerprint(self) -> None:
        a = _make_key()
        b = _make_key(backend_name="claude-code")
        assert a.fingerprint != b.fingerprint

    def test_different_suppressed_rules_changes_fingerprint(self) -> None:
        a = _make_key(suppressed_rules=frozenset())
        b = _make_key(suppressed_rules=frozenset({"rule_a"}))
        assert a.fingerprint != b.fingerprint

    def test_different_defer_unresolved_changes_fingerprint(self) -> None:
        a = _make_key(defer_unresolved=False)
        b = _make_key(defer_unresolved=True)
        assert a.fingerprint != b.fingerprint

    def test_different_kitchen_instance_changes_fingerprint(self) -> None:
        kiid_a = KitchenInstanceId(value="kitchen-a", process_id=1)
        kiid_b = KitchenInstanceId(value="kitchen-b", process_id=1)
        a = _make_key(kitchen_instance_id=kiid_a)
        b = _make_key(kitchen_instance_id=kiid_b)
        assert a.fingerprint != b.fingerprint

    def test_key_is_immutable(self) -> None:
        key = _make_key()
        with pytest.raises(Exception):
            key.recipe_name = "changed"  # type: ignore[misc]

    def test_key_is_hashable(self) -> None:
        key = _make_key()
        assert hash(key) == hash(key)


class TestRecipeCompilationResult:
    def test_construction_with_minimum_fields(self) -> None:
        key = _make_key()
        result = RecipeCompilationResult(
            key=key,
            recipe_name="demo",
            recipe_kind="standard",
            recipe_version="1.0.0",
            content_fingerprint="abc",
            composite_fingerprint="def",
            manifest_fingerprint="ghi",
            invocation_fingerprint="jkl",
            selected_skill_source=SkillSource.BUNDLED,
            payload={"steps": []},
        )
        assert result.recipe_name == "demo"
        assert result.payload == {"steps": []}

    def test_empty_recipe_name_raises(self) -> None:
        key = _make_key()
        with pytest.raises(ValueError, match="recipe_name must be non-empty"):
            RecipeCompilationResult(
                key=key,
                recipe_name="",
                recipe_kind="standard",
                recipe_version="1.0.0",
                content_fingerprint="abc",
                composite_fingerprint="def",
                manifest_fingerprint="ghi",
                invocation_fingerprint="jkl",
                selected_skill_source=SkillSource.BUNDLED,
                payload=None,
            )

    def test_empty_fingerprint_raises(self) -> None:
        key = _make_key()
        with pytest.raises(ValueError, match="content_fingerprint must be non-empty"):
            RecipeCompilationResult(
                key=key,
                recipe_name="demo",
                recipe_kind="standard",
                recipe_version="1.0.0",
                content_fingerprint="",
                composite_fingerprint="def",
                manifest_fingerprint="ghi",
                invocation_fingerprint="jkl",
                selected_skill_source=SkillSource.BUNDLED,
                payload=None,
            )

    def test_result_is_hashable(self) -> None:
        key = _make_key()
        result = RecipeCompilationResult(
            key=key,
            recipe_name="demo",
            recipe_kind="standard",
            recipe_version="1.0.0",
            content_fingerprint="abc",
            composite_fingerprint="def",
            manifest_fingerprint="ghi",
            invocation_fingerprint="jkl",
            selected_skill_source=SkillSource.BUNDLED,
            payload=None,
        )
        assert hash(result) == hash(result)


class TestRecipeCompilationFailure:
    def test_construction_with_minimum_fields(self) -> None:
        key = _make_key()
        failure = RecipeCompilationFailure(
            key=key,
            reason="validation_error",
            diagnostics={"step": "build", "error": "missing input"},
        )
        assert failure.reason == "validation_error"
        assert failure.diagnostics["step"] == "build"

    def test_empty_reason_raises(self) -> None:
        key = _make_key()
        with pytest.raises(ValueError, match="reason must be non-empty"):
            RecipeCompilationFailure(
                key=key,
                reason="",
                diagnostics={},
            )

    def test_is_publishable_is_false(self) -> None:
        key = _make_key()
        failure = RecipeCompilationFailure(
            key=key,
            reason="error",
            diagnostics={},
        )
        assert failure.is_publishable is False

    def test_diagnostics_is_immutable(self) -> None:
        key = _make_key()
        failure = RecipeCompilationFailure(
            key=key,
            reason="error",
            diagnostics={"k": "v"},
        )
        with pytest.raises(TypeError):
            failure.diagnostics["new"] = "value"  # type: ignore[index]


class TestComputeCompilationKeyFingerprint:
    def test_explicit_fingerprint_matches_property(self) -> None:
        key = _make_key()
        assert compute_compilation_key_fingerprint(key) == key.fingerprint

    def test_fingerprint_is_sha256_hex(self) -> None:
        key = _make_key()
        fp = key.fingerprint
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)
