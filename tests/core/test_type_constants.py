"""Tests for PACK_REGISTRY and related constants in core._type_constants."""

from __future__ import annotations

import hashlib
import json
import operator

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_response_backstop_exemption_def_namedtuple_fields() -> None:
    from autoskillit.core import ResponseBackstopExemptionDef

    definition = ResponseBackstopExemptionDef(
        max_chars=1,
        max_utf8_bytes=2,
        measurement_id="measurement-v1",
    )
    assert definition._fields == ("max_chars", "max_utf8_bytes", "measurement_id")
    assert definition.max_chars == 1
    assert definition.max_utf8_bytes == 2
    assert definition.measurement_id == "measurement-v1"


def test_response_backstop_exemption_registry_is_closed_and_pinned() -> None:
    from autoskillit.core import (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
        ResponseBackstopExemptionDef,
    )

    assert RESPONSE_BACKSTOP_EXEMPTION_REGISTRY == {
        "get_recipe_section": ResponseBackstopExemptionDef(
            max_chars=195_000,
            max_utf8_bytes=195_000,
            measurement_id="bundled-recipes-all-modes-2026-08-09/get-recipe-section",
        ),
        "load_recipe": ResponseBackstopExemptionDef(
            max_chars=195_000,
            max_utf8_bytes=195_000,
            measurement_id="bundled-recipes-all-modes-2026-07-22/load-recipe",
        ),
        "open_kitchen": ResponseBackstopExemptionDef(
            max_chars=195_000,
            max_utf8_bytes=195_000,
            measurement_id="bundled-recipes-all-modes-2026-07-22/open-kitchen",
        ),
    }
    with pytest.raises(TypeError):
        operator.setitem(
            RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
            "mutated",
            RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["load_recipe"],
        )


def test_response_backstop_exemption_registry_digest_is_canonical() -> None:
    from autoskillit.core import (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    )

    canonical = {
        tool_name: definition._asdict()
        for tool_name, definition in sorted(RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.items())
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(payload.encode("ascii")).hexdigest() == (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
    )
    assert (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
        == "669328c03372e174282f498e17c682c6e8d74bd68e9b8086e400848777061f66"
    )


def test_response_backstop_exemption_registry_public_gateways() -> None:
    import autoskillit.core as core
    import autoskillit.core.types as core_types

    for module in (core, core_types):
        assert module.RESPONSE_BACKSTOP_EXEMPTION_REGISTRY
        assert module.RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
        assert module.ResponseBackstopExemptionDef
        assert not hasattr(module, "OPEN_KITCHEN_OUTPUT_BUDGET_BYTES")


def test_recipe_execution_install_site_registry_digest_is_canonical() -> None:
    from autoskillit.core import (
        RECIPE_EXECUTION_INSTALL_SITE_REGISTRY,
        RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST,
    )

    canonical = {
        site: definition._asdict()
        for site, definition in sorted(RECIPE_EXECUTION_INSTALL_SITE_REGISTRY.items())
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(payload.encode("ascii")).hexdigest() == (
        RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST
    )
    assert (
        RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST
        == "7aec26971d5946cf50a32c9da3b5c1db10046ec14045881b0339f91993888fe4"
    )


# REQ-PACK-001: PACK_REGISTRY defines all packs with default_enabled
def test_core_packs_constant_defined() -> None:
    """CORE_PACKS must be a frozenset defined in _type_constants and exported via core."""
    from autoskillit.core.types._type_constants_registries import CORE_PACKS

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


def test_audit_pipeline_pack_in_registry() -> None:
    from autoskillit.core import PACK_REGISTRY

    assert "audit-pipeline" in PACK_REGISTRY
    assert PACK_REGISTRY["audit-pipeline"].default_enabled is False
    assert (
        PACK_REGISTRY["audit-pipeline"].description
        == "Audit pipeline internals (recipe-dispatched only)"
    )


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
        "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS",
    }
    assert expected <= AUTOSKILLIT_PRIVATE_ENV_VARS
    assert "AUTOSKILLIT_L3_TOOL_TAGS" not in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_private_env_vars_includes_execution_control_vars() -> None:
    """Execution-control vars must not leak into Codex subprocess environments."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    expected = {
        "SCENARIO_STEP_NAME",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES",
        "MAX_MCP_OUTPUT_TOKENS",
    }
    assert expected <= AUTOSKILLIT_PRIVATE_ENV_VARS


def test_private_env_vars_include_native_shell_lineage_controls() -> None:
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_FORWARD_VARS,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    )

    protected = {
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
    }
    assert protected <= AUTOSKILLIT_PRIVATE_ENV_VARS
    assert protected.isdisjoint(CODEX_MCP_ENV_FORWARD_VARS)


def test_session_deadline_in_private_env_vars() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_SESSION_DEADLINE" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_fleet_inspector_model_crosses_only_declared_child_boundaries() -> None:
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_FORWARD_VARS,
        FLEET_INSPECTOR_MODEL_ENV_VAR,
    )

    assert FLEET_INSPECTOR_MODEL_ENV_VAR == "AUTOSKILLIT_FLEET_INSPECTOR_MODEL"
    assert FLEET_INSPECTOR_MODEL_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS
    assert FLEET_INSPECTOR_MODEL_ENV_VAR in CODEX_MCP_ENV_FORWARD_VARS


def test_codex_mcp_receives_launch_registry_join() -> None:
    from autoskillit.core import (
        AUTOSKILLIT_STATE_ROOT_ENV_VAR,
        CODEX_MCP_ENV_FORWARD_VARS,
        LAUNCH_ID_ENV_VAR,
    )

    assert {LAUNCH_ID_ENV_VAR, AUTOSKILLIT_STATE_ROOT_ENV_VAR} <= CODEX_MCP_ENV_FORWARD_VARS


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
    from autoskillit.core.types import _type_constants

    assert "EXCLUSIVE_FEATURE_TOOLS" not in _type_constants.__all__


def test_fleet_default_enabled_is_false() -> None:
    """Fleet is gated off by default — enabled only via project config."""
    from autoskillit.core import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["fleet"].default_enabled is False


def test_exploration_feature_definition_pins_loading_and_visibility_policy() -> None:
    from autoskillit.core import FEATURE_REGISTRY

    definition = FEATURE_REGISTRY["exploration"]

    assert definition.tier == 1
    assert definition.import_package == "autoskillit.exploration"
    assert definition.tool_tags == frozenset({"exploration"})
    assert definition.default_enabled is False
    assert definition.requires_backend_alignment is False


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
    from autoskillit.core.types._type_constants_registries import FLEET_MENU_TOOLS

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

    from autoskillit.core.types._type_constants_features import FeatureDef

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
            "reset_dispatch",
        }
    )
    assert FLEET_TOOLS == expected, "Update expected set when FLEET_TOOLS changes"


def test_skill_tools_matches_expected() -> None:
    """SKILL_TOOLS must match a hardcoded expected set."""
    from autoskillit.core import SKILL_TOOLS

    assert SKILL_TOOLS == frozenset({"run_skill"})


def test_config_authority_keys_constant() -> None:
    """CONFIG_AUTHORITY_KEYS must contain exactly the six config-authority keys."""
    from autoskillit.core import CONFIG_AUTHORITY_KEYS

    assert isinstance(CONFIG_AUTHORITY_KEYS, frozenset)
    assert CONFIG_AUTHORITY_KEYS == frozenset(
        {
            "source_dir",
            "base_branch",
            "local_review_rounds",
            "adversarial_review_level",
            "is_fleet_dispatch",
            "dispatch_id",
        }
    )


def test_provider_profile_in_private_env_vars() -> None:
    """PROVIDER_PROFILE_ENV_VAR is the canonical name for the provider-profile env var
    and must be a member of AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS, PROVIDER_PROFILE_ENV_VAR

    assert PROVIDER_PROFILE_ENV_VAR == "AUTOSKILLIT_PROVIDER_PROFILE"
    assert PROVIDER_PROFILE_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_headless_tools_contains_expected_names():
    from autoskillit.core.types import HEADLESS_TOOLS

    assert HEADLESS_TOOLS == {
        "test_check",
        "unlock_agent_pack",
        "commit_files",
        "post_pr_review",
        "write_audit_semantic_result",
        "write_standalone_audit_evidence",
        "write_audit_disposition_bundle",
        "delegate_evidence_reader",
    }


def test_evidence_reader_tools_are_exact_internal_subset() -> None:
    import autoskillit.core as core
    import autoskillit.core.types as core_types
    from autoskillit.core.types import (
        ALL_VISIBILITY_TAGS,
        EVIDENCE_READER_TOOLS,
        FREE_RANGE_TOOLS,
        GATED_TOOLS,
        HEADLESS_TOOLS,
        TOOL_SUBSET_TAGS,
    )

    assert EVIDENCE_READER_TOOLS == frozenset(
        {
            "read_authorized_artifact",
            "get_authorized_artifact_page",
        }
    )
    assert EVIDENCE_READER_TOOLS <= GATED_TOOLS
    assert EVIDENCE_READER_TOOLS.isdisjoint(FREE_RANGE_TOOLS | HEADLESS_TOOLS)
    assert "delegate_evidence_reader" in HEADLESS_TOOLS
    assert "delegate_evidence_reader" not in GATED_TOOLS
    assert "delegate_evidence_reader" not in FREE_RANGE_TOOLS
    assert "evidence-reader" in ALL_VISIBILITY_TAGS
    assert {tool_name: TOOL_SUBSET_TAGS[tool_name] for tool_name in EVIDENCE_READER_TOOLS} == {
        "read_authorized_artifact": frozenset({"evidence-reader"}),
        "get_authorized_artifact_page": frozenset({"evidence-reader"}),
    }
    assert core.EVIDENCE_READER_TOOLS is EVIDENCE_READER_TOOLS
    assert core_types.EVIDENCE_READER_TOOLS is EVIDENCE_READER_TOOLS


def test_free_range_tools_contains_expected_names():
    from autoskillit.core.types import FREE_RANGE_TOOLS

    assert FREE_RANGE_TOOLS == {
        "open_kitchen",
        "close_kitchen",
        "disable_quota_guard",
        "enable_exploration",
        "reload_session",
        "configure_fleet",
        "configure_order",
        "lock_ingredients",
        "declare_join_batch",
    }


# ---------------------------------------------------------------------------
# T4: SESSION_TYPE_COOK/ORDER moved to cli/session/_session_constants.py
# ---------------------------------------------------------------------------


def test_session_type_cook_order_not_in_core_types() -> None:
    """SESSION_TYPE_COOK/ORDER are CLI labels, not core type constants."""
    from autoskillit.core.types import _type_constants

    assert not hasattr(_type_constants, "SESSION_TYPE_COOK")
    assert not hasattr(_type_constants, "SESSION_TYPE_ORDER")


# ---------------------------------------------------------------------------
# T1: CODEX_SCHEMA_VERSION constant exists with correct value
# ---------------------------------------------------------------------------


def test_codex_schema_version_value() -> None:
    from autoskillit.core.types._type_constants_env import CODEX_SCHEMA_VERSION

    assert CODEX_SCHEMA_VERSION == 2
    assert isinstance(CODEX_SCHEMA_VERSION, int)


# ---------------------------------------------------------------------------
# T2: CODEX_SCHEMA_VERSION is in __all__
# ---------------------------------------------------------------------------


def test_codex_schema_version_in_all() -> None:
    from autoskillit.core.types._type_constants_env import __all__ as env_all

    assert "CODEX_SCHEMA_VERSION" in env_all


# ---------------------------------------------------------------------------
# T3: CODEX_SCHEMA_VERSION re-exported via types hub
# ---------------------------------------------------------------------------


def test_codex_schema_version_importable_from_types() -> None:
    from autoskillit.core.types import CODEX_SCHEMA_VERSION

    assert CODEX_SCHEMA_VERSION == 2


# ---------------------------------------------------------------------------
# T4: CODEX_SCHEMA_VERSION re-exported via core gateway
# ---------------------------------------------------------------------------


def test_codex_schema_version_importable_from_core() -> None:
    from autoskillit.core import CODEX_SCHEMA_VERSION

    assert CODEX_SCHEMA_VERSION == 2


# ---------------------------------------------------------------------------
# T1: CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR constant exists with correct value
# ---------------------------------------------------------------------------


def test_claude_code_mcp_tool_idle_timeout_env_var_value() -> None:
    from autoskillit.core.types._type_constants_env import (
        CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR,
    )

    assert CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR == "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT"
    assert isinstance(CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR, str)


# ---------------------------------------------------------------------------
# T2: CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR is in __all__
# ---------------------------------------------------------------------------


def test_claude_code_mcp_tool_idle_timeout_env_var_in_all() -> None:
    from autoskillit.core.types._type_constants_env import __all__ as env_all

    assert "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR" in env_all


# ---------------------------------------------------------------------------
# T3: CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR re-exported via types hub
# ---------------------------------------------------------------------------


def test_claude_code_mcp_tool_idle_timeout_env_var_importable_from_types() -> None:
    from autoskillit.core.types import CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR

    assert CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR == "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT"


# ---------------------------------------------------------------------------
# T4: CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR re-exported via core gateway
# ---------------------------------------------------------------------------


def test_claude_code_mcp_tool_idle_timeout_env_var_importable_from_core() -> None:
    from autoskillit.core import CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR

    assert CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR == "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT"


# ---------------------------------------------------------------------------
# HEADLESS_AUTO_GATE_ENV_VAR constant
# ---------------------------------------------------------------------------


def test_headless_auto_gate_env_var_value() -> None:
    from autoskillit.core.types._type_constants_env import HEADLESS_AUTO_GATE_ENV_VAR

    assert HEADLESS_AUTO_GATE_ENV_VAR == "AUTOSKILLIT_HEADLESS_AUTO_GATE"


def test_headless_auto_gate_env_var_in_all() -> None:
    from autoskillit.core.types._type_constants_env import __all__ as env_all

    assert "HEADLESS_AUTO_GATE_ENV_VAR" in env_all


def test_headless_auto_gate_env_var_importable_from_types() -> None:
    from autoskillit.core.types import HEADLESS_AUTO_GATE_ENV_VAR

    assert HEADLESS_AUTO_GATE_ENV_VAR == "AUTOSKILLIT_HEADLESS_AUTO_GATE"


def test_headless_auto_gate_env_var_importable_from_core() -> None:
    from autoskillit.core import HEADLESS_AUTO_GATE_ENV_VAR

    assert HEADLESS_AUTO_GATE_ENV_VAR == "AUTOSKILLIT_HEADLESS_AUTO_GATE"


def test_headless_auto_gate_env_var_in_private_env_vars() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_HEADLESS_AUTO_GATE" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_scenario_step_name_in_private_env_vars() -> None:
    """SCENARIO_STEP_NAME must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "SCENARIO_STEP_NAME" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_autoskillit_allowed_write_prefix_in_private_env_vars() -> None:
    """AUTOSKILLIT_ALLOWED_WRITE_PREFIX must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_autoskillit_allowed_write_prefixes_in_private_env_vars() -> None:
    """AUTOSKILLIT_ALLOWED_WRITE_PREFIXES must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_max_mcp_output_tokens_in_private_env_vars() -> None:
    """MAX_MCP_OUTPUT_TOKENS must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "MAX_MCP_OUTPUT_TOKENS" in AUTOSKILLIT_PRIVATE_ENV_VARS


# ---------------------------------------------------------------------------
# T1: Quota trigger constants exported from core
# ---------------------------------------------------------------------------


def test_quota_trigger_constants_exported() -> None:
    """All four QUOTA_* trigger constants must be importable from autoskillit.core."""
    from autoskillit.core import (
        QUOTA_BUDGET_EXCEEDED_TRIGGER,
        QUOTA_GUARD_DENY_TRIGGER,
        QUOTA_POST_BUDGET_EXCEEDED_TRIGGER,
        QUOTA_POST_WARNING_TRIGGER,
    )

    assert isinstance(QUOTA_GUARD_DENY_TRIGGER, str)
    assert isinstance(QUOTA_BUDGET_EXCEEDED_TRIGGER, str)
    assert isinstance(QUOTA_POST_WARNING_TRIGGER, str)
    assert isinstance(QUOTA_POST_BUDGET_EXCEEDED_TRIGGER, str)


# ---------------------------------------------------------------------------
# INVESTIGATION_COMPLETE_MARKER constant (T1)
# ---------------------------------------------------------------------------


def test_investigation_complete_marker_defined() -> None:
    """INVESTIGATION_COMPLETE_MARKER must be defined and exported from autoskillit.core."""
    from autoskillit.core import INVESTIGATION_COMPLETE_MARKER

    assert INVESTIGATION_COMPLETE_MARKER == "<!-- investigation_complete: true -->"


def test_investigation_complete_marker_in_all() -> None:
    """INVESTIGATION_COMPLETE_MARKER must be in _type_constants.__all__."""
    from autoskillit.core.types import _type_constants

    assert "INVESTIGATION_COMPLETE_MARKER" in _type_constants.__all__


# ---------------------------------------------------------------------------
# AUTOSKILLIT_CWD membership in AUTOSKILLIT_PRIVATE_ENV_VARS (T1)
# ---------------------------------------------------------------------------


def test_autoskillit_cwd_in_private_env_vars() -> None:
    """AUTOSKILLIT_CWD must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_CWD" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_autoskillit_write_guard_tool_names_in_private_env_vars() -> None:
    """AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES must be scrubbed from ambient env."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES" in AUTOSKILLIT_PRIVATE_ENV_VARS


# ---------------------------------------------------------------------------
# ORDER_INTERACTIVE_REQUIRED_ENV constant (issue #4253 Part A)
# ---------------------------------------------------------------------------


def test_order_interactive_required_env_value() -> None:
    from autoskillit.core.types._type_constants_env import ORDER_INTERACTIVE_REQUIRED_ENV

    assert ORDER_INTERACTIVE_REQUIRED_ENV == frozenset(
        {
            "AUTOSKILLIT_SESSION_TYPE",
            "MAX_MCP_OUTPUT_TOKENS",
            "MCP_CONNECTION_NONBLOCKING",
            "AUTOSKILLIT_AGENT_BACKEND",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND",
        }
    )


def test_order_interactive_required_env_excludes_headless() -> None:
    """Interactive order sessions must not require AUTOSKILLIT_HEADLESS."""
    from autoskillit.core.types._type_constants_env import ORDER_INTERACTIVE_REQUIRED_ENV

    assert "AUTOSKILLIT_HEADLESS" not in ORDER_INTERACTIVE_REQUIRED_ENV


def test_order_interactive_required_env_in_all() -> None:
    from autoskillit.core.types._type_constants_env import __all__ as env_all

    assert "ORDER_INTERACTIVE_REQUIRED_ENV" in env_all


def test_order_interactive_required_env_importable_from_types() -> None:
    from autoskillit.core.types import ORDER_INTERACTIVE_REQUIRED_ENV

    assert "MAX_MCP_OUTPUT_TOKENS" in ORDER_INTERACTIVE_REQUIRED_ENV


def test_order_interactive_required_env_importable_from_core() -> None:
    from autoskillit.core import ORDER_INTERACTIVE_REQUIRED_ENV

    assert "MAX_MCP_OUTPUT_TOKENS" in ORDER_INTERACTIVE_REQUIRED_ENV


# ---------------------------------------------------------------------------
# CODEX_INTERACTIVE_REQUIRED_ENV includes MAX_MCP_OUTPUT_TOKENS (issue #4253 Part A)
# ---------------------------------------------------------------------------


def test_codex_interactive_required_env_includes_max_mcp_output_tokens() -> None:
    from autoskillit.core import CODEX_INTERACTIVE_REQUIRED_ENV

    assert "MAX_MCP_OUTPUT_TOKENS" in CODEX_INTERACTIVE_REQUIRED_ENV


def test_codex_cook_storage_and_environment_constants_are_pinned() -> None:
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_ACTIVE_VIEWS_SUBDIR,
        CODEX_ARCHIVED_SESSIONS_SUBDIR,
        CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR,
        CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR,
        CODEX_COOK_RESERVED_ENV_VARS,
        CODEX_SESSIONS_SUBDIR,
        CODEX_STARTUP_TRACE_ENV_VAR,
    )

    assert (
        CODEX_SESSIONS_SUBDIR,
        CODEX_ARCHIVED_SESSIONS_SUBDIR,
        CODEX_ACTIVE_VIEWS_SUBDIR,
        CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR,
        CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR,
    ) == (
        "codex-sessions",
        "codex-archived-sessions",
        "codex-active-sessions",
        "codex-attempt-reconciliations",
        "codex-attempt-reconciliation-tombstones",
    )
    assert CODEX_COOK_RESERVED_ENV_VARS == frozenset({"CODEX_HOME", "CODEX_SQLITE_HOME"})
    assert CODEX_STARTUP_TRACE_ENV_VAR == "AUTOSKILLIT_CODEX_STARTUP_TRACE"
    assert {
        CODEX_STARTUP_TRACE_ENV_VAR,
        *CODEX_COOK_RESERVED_ENV_VARS,
    } <= AUTOSKILLIT_PRIVATE_ENV_VARS
