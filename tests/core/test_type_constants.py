"""Tests for PACK_REGISTRY and related constants in core._type_constants."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


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


def test_kitchen_core_in_pack_registry() -> None:
    """kitchen-core is a registered pack with default_enabled=True."""
    from autoskillit.core.types import PACK_REGISTRY

    assert "kitchen-core" in PACK_REGISTRY
    assert PACK_REGISTRY["kitchen-core"].default_enabled is True


def test_private_env_vars_includes_franchise_tier_vars() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    expected = {
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_KITCHEN_SESSION_ID",
        "AUTOSKILLIT_CAMPAIGN_STATE_PATH",
        "AUTOSKILLIT_PROJECT_DIR",
        "AUTOSKILLIT_L2_TOOL_TAGS",
    }
    assert expected <= AUTOSKILLIT_PRIVATE_ENV_VARS


def test_campaign_id_env_var_and_kitchen_session_id_env_var_exported_from_core() -> None:
    """CAMPAIGN_ID_ENV_VAR and KITCHEN_SESSION_ID_ENV_VAR are re-exported from autoskillit.core."""
    from autoskillit.core import CAMPAIGN_ID_ENV_VAR, KITCHEN_SESSION_ID_ENV_VAR

    assert CAMPAIGN_ID_ENV_VAR == "AUTOSKILLIT_CAMPAIGN_ID"
    assert KITCHEN_SESSION_ID_ENV_VAR == "AUTOSKILLIT_KITCHEN_SESSION_ID"


# ---------------------------------------------------------------------------
# RECIPE_PACK_REGISTRY and RecipePackDef (franchise schema extension)
# ---------------------------------------------------------------------------


def test_recipe_pack_def_namedtuple_fields() -> None:
    from autoskillit.core import RecipePackDef

    rpd = RecipePackDef(default_enabled=True, description="test pack")
    assert rpd.default_enabled is True
    assert rpd.description == "test pack"


def test_recipe_pack_registry_has_three_entries() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY, RecipePackDef

    assert len(RECIPE_PACK_REGISTRY) == 3
    assert all(isinstance(v, RecipePackDef) for v in RECIPE_PACK_REGISTRY.values())


def test_recipe_pack_registry_implementation_family() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY

    assert "implementation-family" in RECIPE_PACK_REGISTRY
    assert RECIPE_PACK_REGISTRY["implementation-family"].default_enabled is True
    assert (
        RECIPE_PACK_REGISTRY["implementation-family"].description
        == "Implementation and refactoring recipes"
    )


def test_recipe_pack_registry_research_family() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY

    assert "research-family" in RECIPE_PACK_REGISTRY
    assert RECIPE_PACK_REGISTRY["research-family"].default_enabled is False
    assert (
        RECIPE_PACK_REGISTRY["research-family"].description == "Research and exploration recipes"
    )


def test_recipe_pack_registry_orchestration_family() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY

    assert "orchestration-family" in RECIPE_PACK_REGISTRY
    assert RECIPE_PACK_REGISTRY["orchestration-family"].default_enabled is True
    assert (
        RECIPE_PACK_REGISTRY["orchestration-family"].description
        == "Campaign orchestration and automation"
    )


def test_recipe_pack_def_importable_from_core() -> None:
    from autoskillit.core import RECIPE_PACK_REGISTRY, RecipePackDef

    assert isinstance(RECIPE_PACK_REGISTRY, dict)
    assert all(isinstance(v, RecipePackDef) for v in RECIPE_PACK_REGISTRY.values())


def test_feature_reveal_tags_exists() -> None:
    from autoskillit.core import FEATURE_REVEAL_TAGS

    assert isinstance(FEATURE_REVEAL_TAGS, frozenset)
    assert "fleet" in FEATURE_REVEAL_TAGS


def test_exclusive_feature_tools_removed() -> None:
    """EXCLUSIVE_FEATURE_TOOLS was removed (issue #1150) — must not be importable."""
    import autoskillit.core as core

    assert not hasattr(core, "EXCLUSIVE_FEATURE_TOOLS")


def test_exclusive_feature_tools_not_in_all() -> None:
    """EXCLUSIVE_FEATURE_TOOLS must not appear in _type_constants.__all__."""
    from autoskillit.core import _type_constants

    assert "EXCLUSIVE_FEATURE_TOOLS" not in _type_constants.__all__


def test_fleet_default_enabled_is_false() -> None:
    """Fleet is gated off by default — enabled only via project config."""
    from autoskillit.core import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["fleet"].default_enabled is False


def test_is_feature_enabled_fleet_defaults_false() -> None:
    """Without explicit config, fleet resolves to disabled."""
    from autoskillit.core.feature_flags import is_feature_enabled

    assert is_feature_enabled("fleet", {}) is False


def test_fleet_dispatch_tools_constant_exists() -> None:
    """FLEET_DISPATCH_TOOLS is a frozenset of exactly the 4 fleet-dispatch discovery tools."""
    from autoskillit.core._type_constants import FLEET_DISPATCH_TOOLS

    assert isinstance(FLEET_DISPATCH_TOOLS, frozenset)
    assert FLEET_DISPATCH_TOOLS == frozenset(
        {
            "list_recipes",
            "load_recipe",
            "fetch_github_issue",
            "get_issue_title",
        }
    )
