"""Tests for PACK_REGISTRY and related constants in core._type_constants."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


# REQ-PACK-001: PACK_REGISTRY defines all packs with default_enabled
def test_core_packs_constant_defined() -> None:
    """CORE_PACKS must be a frozenset defined in _type_constants and exported via core."""
    from autoskillit.core.types._type_constants import CORE_PACKS

    assert isinstance(CORE_PACKS, frozenset)
    assert CORE_PACKS == frozenset({"github", "ci", "clone", "telemetry"})


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


def test_feature_reveal_tags_removed() -> None:
    """FEATURE_REVEAL_TAGS was intentionally removed in #1141."""
    with pytest.raises(ImportError):
        from autoskillit.core import FEATURE_REVEAL_TAGS  # noqa: F401


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
    """Without explicit config, fleet resolves to disabled when experimental_enabled=False."""
    from autoskillit.core.feature_flags import is_feature_enabled

    assert is_feature_enabled("fleet", {}, experimental_enabled=False) is False
    # fleet is EXPERIMENTAL, so blanket enables it
    assert is_feature_enabled("fleet", {}, experimental_enabled=True) is True


def test_fleet_dispatch_tools_constant_exists() -> None:
    """FLEET_DISPATCH_TOOLS is a frozenset of exactly the 4 fleet-dispatch discovery tools."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS

    assert FLEET_DISPATCH_TOOLS == frozenset(
        {
            "list_recipes",
            "load_recipe",
            "fetch_github_issue",
            "get_issue_title",
        }
    )


def test_fleet_menu_tools_in_type_constants() -> None:
    """FLEET_MENU_TOOLS must live in core._type_constants, not fleet.__init__."""
    from autoskillit.core.types._type_constants import FLEET_MENU_TOOLS

    assert isinstance(FLEET_MENU_TOOLS, tuple)
    assert "dispatch_food_truck" in FLEET_MENU_TOOLS


def test_fleet_menu_tools_not_in_fleet_init() -> None:
    """FLEET_MENU_TOOLS must no longer be exported from fleet.__init__."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import autoskillit.fleet as f; print(hasattr(f, 'FLEET_MENU_TOOLS'))",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Subprocess failed:\n{result.stderr}"
    assert result.stdout.strip() == "False", (
        "FLEET_MENU_TOOLS still lives in fleet.__init__; move it to core._type_constants"
    )


# ---------------------------------------------------------------------------
# T1: FeatureDef has no redundant name field (Finding 3)
# ---------------------------------------------------------------------------


def test_feature_def_has_no_name_field() -> None:
    """FeatureDef.name is redundant with the FEATURE_REGISTRY dict key and must not exist."""
    import dataclasses

    from autoskillit.core.types._type_constants import FeatureDef

    field_names = {f.name for f in dataclasses.fields(FeatureDef)}
    assert "name" not in field_names, "FeatureDef.name is redundant with FEATURE_REGISTRY dict key"


def test_fleet_tools_matches_expected() -> None:
    """FLEET_TOOLS must match a hardcoded expected set — not derived from tags."""
    from autoskillit.core import FLEET_TOOLS

    expected = frozenset(
        {
            "batch_cleanup_clones",
            "get_pipeline_report",
            "get_token_summary",
            "get_timing_summary",
            "get_quota_events",
            "dispatch_food_truck",
            "record_gate_dispatch",
        }
    )
    assert FLEET_TOOLS == expected, "Update expected set when FLEET_TOOLS changes"


def test_skill_tools_matches_expected() -> None:
    """SKILL_TOOLS must match a hardcoded expected set."""
    from autoskillit.core import SKILL_TOOLS

    assert SKILL_TOOLS == frozenset({"run_skill"})
