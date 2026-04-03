"""Tests for PACK_REGISTRY and related constants in core._type_constants."""

from __future__ import annotations


# REQ-PACK-001: PACK_REGISTRY defines all packs with default_enabled
def test_pack_registry_contains_all_packs() -> None:
    from autoskillit.core import PACK_REGISTRY

    assert "github" in PACK_REGISTRY
    assert "research" in PACK_REGISTRY
    assert PACK_REGISTRY["github"].default_enabled is True
    assert PACK_REGISTRY["research"].default_enabled is False


# REQ-PACK-002: CATEGORY_TAGS derived from PACK_REGISTRY.keys()
def test_category_tags_derived_from_pack_registry() -> None:
    from autoskillit.core import CATEGORY_TAGS, PACK_REGISTRY

    assert CATEGORY_TAGS == frozenset(PACK_REGISTRY.keys())


def test_pack_registry_is_superset_of_old_category_tags() -> None:
    from autoskillit.core import PACK_REGISTRY

    old_tags = {"github", "ci", "clone", "telemetry", "arch-lens", "audit"}
    assert old_tags.issubset(PACK_REGISTRY.keys())


def test_pack_def_namedtuple_fields() -> None:
    from autoskillit.core import PackDef

    pd = PackDef(default_enabled=False, description="test")
    assert pd.default_enabled is False
    assert pd.description == "test"


def test_pack_registry_new_packs_are_default_disabled() -> None:
    from autoskillit.core import PACK_REGISTRY

    assert "exp-lens" in PACK_REGISTRY
    assert PACK_REGISTRY["exp-lens"].default_enabled is False


def test_pack_registry_importable_from_core() -> None:
    from autoskillit.core import PACK_REGISTRY, PackDef

    assert isinstance(PACK_REGISTRY, dict)
    assert all(isinstance(v, PackDef) for v in PACK_REGISTRY.values())
