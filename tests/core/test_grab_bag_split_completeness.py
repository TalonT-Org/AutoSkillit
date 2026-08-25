"""Structural guard tests for the test_types.py / test_type_constants.py grab-bag split.

Every test in this file is a structural guard. They prove that:
1. Every pre-split test function/class was moved to *some* new test file.
2. No pre-split test name was duplicated across new files.
3. The new test files satisfy project conventions (layer marker, size marker,
   no shadow of conftest autouse fixture).
4. The pre-split source files were deleted.
"""

from __future__ import annotations

import ast
import importlib
import sys
from collections import Counter
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _bootstrap_tests_on_sys_path() -> None:
    """Ensure the tests/ root is on sys.path so tests.core imports resolve."""
    tests_root = str(Path(__file__).resolve().parents[2] / "tests")
    if tests_root not in sys.path:
        sys.path.insert(0, tests_root)


def _module_defines_name(module: object, name: str) -> bool:
    """Return True iff ``name`` is defined directly in ``module`` (not just imported into it).

    Implementation: AST-walks the module's source file looking for a top-level
    ``FunctionDef``/``AsyncFunctionDef``/``ClassDef`` (or a pytest parametrize
    decorator attached to one) with the matching name. This rejects re-exports
    via ``from <other_module> import name``, which would otherwise bind ``name``
    into ``module.__dict__`` and falsely satisfy a plain membership check.
    """
    try:
        module_file = getattr(module, "__file__", None)
    except Exception:
        module_file = None
    if not module_file or not isinstance(module_file, str):
        return False
    try:
        source = Path(module_file).read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        ):
            return True
    return False


def _source_imports_autoskillit_execution(source: str) -> bool:
    """AST-detect any import of the ``autoskillit.execution`` subpackage in ``source``."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "autoskillit.execution" or module.startswith("autoskillit.execution."):
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "autoskillit.execution" or alias.name.startswith(
                    "autoskillit.execution."
                ):
                    return True
    return False


def _source_defines_clear_snapshot_cache(source: str) -> bool:
    """AST-detect shadowing of the conftest _clear_snapshot_cache fixture."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_clear_snapshot_cache"
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Pre-split inventories (frozen)
# ---------------------------------------------------------------------------


_PRE_SPLIT_TEST_TYPES_NAMES: frozenset[str] = frozenset(
    {
        # test_types_enums.py
        "test_claude_content_block_type_from_api",
        "test_retry_reason_values",
        "test_merge_failed_step_values",
        "test_merge_state_values",
        "test_restart_scope_values",
        "test_channel_confirmation_values",
        "test_session_outcome_is_str_enum_with_expected_values",
        "test_session_outcome_accessible_from_core",
        "test_session_outcome_in_core_all",
        "test_severity_has_ok_member",
        "test_severity_enum_not_equal_to_uppercase_string",
        "test_hook_trust_policy_values_and_public_exports",
        "test_pr_state_enum_members_are_locked",
        # test_types_skill_result.py
        "test_skill_result_cancelled_factory",
        "test_skill_result_outcome",
        "test_skill_result_to_json_excludes_outcome",
        "test_skill_result_to_json_includes_worktree_path_when_set",
        "test_skill_result_to_json_omits_worktree_path_when_none",
        "test_skill_result_to_json_preserves_nested_execution_identity",
        "test_git_writes_detected_in_has_progress_evidence",
        "test_skill_result_git_writes_detected_in_json",
        "test_skill_result_git_writes_detected_false_included",
        "test_skill_result_file_changes_count_in_json",
        "test_skill_result_infrastructure_fault_factory",
        "TestSkillResultCrashedFactory",
        "TestSkillResultInfeasibleFactory",
        "TestSkillResultProviderFields",
        "TestInfraOutcome",
        "TestSkillResultExtensionBundles",
        # test_types_protocols.py
        "test_managed_session_home_frozen_slots_exact_fields_and_exports",
        "test_github_fetcher_protocol_has_label_methods",
        "test_subprocess_result_has_elapsed_seconds_field",
        "test_subprocess_runner_protocol_pty_mode_default_false",
        "test_subprocess_runner_protocol_marker_dir_default_none",
        "test_subprocess_runner_protocol_session_id_default_none",
        "test_subprocess_runner_protocol_marker_params_after_max_extension",
        "test_subprocess_runner_protocol_marker_params_are_keyword_only",
        "test_ci_run_scope_event_field",
        "test_ci_run_scope_event_defaults_to_none",
        # test_types_infrastructure_faults.py
        "test_skill_command_prefix_constant_exists",
        "test_autoskillit_skill_prefix_constant_exists",
        "test_write_expected_skills_frozenset_removed",
        "test_write_behavior_spec_dataclass",
        "test_infrastructure_fault_exceptions_share_marker_base",
    }
)

_PRE_SPLIT_TEST_TYPE_CONSTANTS_NAMES: frozenset[str] = frozenset(
    {
        # test_type_constants_response_backstop.py
        "test_response_backstop_exemption_def_namedtuple_fields",
        "test_response_backstop_exemption_registry_is_closed_and_pinned",
        "test_response_backstop_exemption_registry_digest_is_canonical",
        "test_response_backstop_exemption_registry_public_gateways",
        "test_recipe_execution_install_site_registry_digest_is_canonical",
        # test_type_constants_pack_registry.py
        "test_core_packs_constant_defined",
        "test_pack_registry_contains_all_packs",
        "test_category_tags_derived_from_pack_registry",
        "test_pack_registry_is_superset_of_old_category_tags",
        "test_pack_def_namedtuple_fields",
        "test_pack_registry_new_packs_are_default_disabled",
        "test_audit_pipeline_pack_in_registry",
        "test_pack_registry_importable_from_core",
        "test_kitchen_core_in_pack_registry",
        # test_type_constants_private_env_vars.py
        "test_private_env_vars_includes_franchise_tier_vars",
        "test_private_env_vars_includes_execution_control_vars",
        "test_private_env_vars_include_native_shell_lineage_controls",
        "test_session_deadline_in_private_env_vars",
        "test_fleet_inspector_model_crosses_only_declared_child_boundaries",
        "test_codex_mcp_receives_launch_registry_join",
        "test_campaign_id_env_var_and_kitchen_session_id_env_var_exported_from_core",
        "test_provider_profile_in_private_env_vars",
        "test_scenario_step_name_in_private_env_vars",
        "test_autoskillit_allowed_write_prefix_in_private_env_vars",
        "test_autoskillit_allowed_write_prefixes_in_private_env_vars",
        "test_max_mcp_output_tokens_in_private_env_vars",
        "test_autoskillit_cwd_in_private_env_vars",
        "test_autoskillit_write_guard_tool_names_in_private_env_vars",
        # test_type_constants_recipe_pack.py
        "test_recipe_pack_def_namedtuple_fields",
        "test_recipe_pack_registry_has_three_entries",
        "test_recipe_pack_registry_implementation_family",
        "test_recipe_pack_registry_research_family",
        "test_recipe_pack_registry_orchestration_family",
        "test_recipe_pack_def_importable_from_core",
        # test_type_constants_feature_registry.py
        "test_feature_reveal_tags_removed",
        "test_exclusive_feature_tools_removed",
        "test_exclusive_feature_tools_not_in_all",
        "test_fleet_default_enabled_is_false",
        "test_exploration_feature_definition_pins_loading_and_visibility_policy",
        "test_is_feature_enabled_fleet_defaults_false",
        "test_feature_def_has_no_name_field",
        # test_type_constants_fleet_tools.py
        "test_fleet_dispatch_tools_constant_exists",
        "test_fleet_menu_tools_in_type_constants",
        "test_fleet_menu_tools_not_in_fleet_init",
        "test_fleet_tools_matches_expected",
        "test_skill_tools_matches_expected",
        "test_config_authority_keys_constant",
        # test_type_constants_tool_classification.py
        "test_headless_tools_contains_expected_names",
        "test_evidence_reader_tools_are_exact_internal_subset",
        "test_free_range_tools_contains_expected_names",
        # test_type_constants_env.py
        "test_session_type_cook_order_not_in_core_types",
        "test_codex_schema_version_value",
        "test_codex_schema_version_in_all",
        "test_codex_schema_version_importable_from_types",
        "test_codex_schema_version_importable_from_core",
        "test_claude_code_mcp_tool_idle_timeout_env_var_value",
        "test_claude_code_mcp_tool_idle_timeout_env_var_in_all",
        "test_claude_code_mcp_tool_idle_timeout_env_var_importable_from_types",
        "test_claude_code_mcp_tool_idle_timeout_env_var_importable_from_core",
        "test_headless_auto_gate_env_var_value",
        "test_headless_auto_gate_env_var_in_all",
        "test_headless_auto_gate_env_var_importable_from_types",
        "test_headless_auto_gate_env_var_importable_from_core",
        "test_headless_auto_gate_env_var_in_private_env_vars",
        "test_order_interactive_required_env_value",
        "test_order_interactive_required_env_excludes_headless",
        "test_order_interactive_required_env_in_all",
        "test_order_interactive_required_env_importable_from_types",
        "test_order_interactive_required_env_importable_from_core",
        "test_codex_interactive_required_env_includes_max_mcp_output_tokens",
        "test_codex_cook_storage_and_environment_constants_are_pinned",
        # test_type_constants_misc_markers.py
        "test_quota_trigger_constants_exported",
        "test_investigation_complete_marker_defined",
        "test_investigation_complete_marker_in_all",
    }
)

_SPLIT_TARGETS: dict[str, str] = {  # noqa: E501 — table-style mapping, one test name per line
    # test_types.py → 4 files
    "test_claude_content_block_type_from_api": "tests.core.test_types_enums",
    "test_retry_reason_values": "tests.core.test_types_enums",
    "test_merge_failed_step_values": "tests.core.test_types_enums",
    "test_merge_state_values": "tests.core.test_types_enums",
    "test_restart_scope_values": "tests.core.test_types_enums",
    "test_channel_confirmation_values": "tests.core.test_types_enums",
    "test_session_outcome_is_str_enum_with_expected_values": "tests.core.test_types_enums",
    "test_session_outcome_accessible_from_core": "tests.core.test_types_enums",
    "test_session_outcome_in_core_all": "tests.core.test_types_enums",
    "test_severity_has_ok_member": "tests.core.test_types_enums",
    "test_severity_enum_not_equal_to_uppercase_string": "tests.core.test_types_enums",
    "test_hook_trust_policy_values_and_public_exports": "tests.core.test_types_enums",
    "test_pr_state_enum_members_are_locked": "tests.core.test_types_enums",
    "test_skill_result_cancelled_factory": "tests.core.test_types_skill_result",
    "test_skill_result_outcome": "tests.core.test_types_skill_result",
    "test_skill_result_to_json_excludes_outcome": "tests.core.test_types_skill_result",
    "test_skill_result_to_json_includes_worktree_path_when_set": "tests.core.test_types_skill_result",  # noqa: E501
    "test_skill_result_to_json_omits_worktree_path_when_none": "tests.core.test_types_skill_result",  # noqa: E501
    "test_skill_result_to_json_preserves_nested_execution_identity": "tests.core.test_types_skill_result",  # noqa: E501
    "test_git_writes_detected_in_has_progress_evidence": "tests.core.test_types_skill_result",
    "test_skill_result_git_writes_detected_in_json": "tests.core.test_types_skill_result",
    "test_skill_result_git_writes_detected_false_included": "tests.core.test_types_skill_result",
    "test_skill_result_file_changes_count_in_json": "tests.core.test_types_skill_result",
    "test_skill_result_infrastructure_fault_factory": "tests.core.test_types_skill_result",
    "TestSkillResultCrashedFactory": "tests.core.test_types_skill_result",
    "TestSkillResultInfeasibleFactory": "tests.core.test_types_skill_result",
    "TestSkillResultProviderFields": "tests.core.test_types_skill_result",
    "TestInfraOutcome": "tests.core.test_types_skill_result",
    "TestSkillResultExtensionBundles": "tests.core.test_types_skill_result",
    "test_managed_session_home_frozen_slots_exact_fields_and_exports": "tests.core.test_types_protocols",  # noqa: E501
    "test_github_fetcher_protocol_has_label_methods": "tests.core.test_types_protocols",
    "test_subprocess_result_has_elapsed_seconds_field": "tests.core.test_types_protocols",
    "test_subprocess_runner_protocol_pty_mode_default_false": "tests.core.test_types_protocols",
    "test_subprocess_runner_protocol_marker_dir_default_none": "tests.core.test_types_protocols",
    "test_subprocess_runner_protocol_session_id_default_none": "tests.core.test_types_protocols",
    "test_subprocess_runner_protocol_marker_params_after_max_extension": "tests.core.test_types_protocols",  # noqa: E501
    "test_subprocess_runner_protocol_marker_params_are_keyword_only": "tests.core.test_types_protocols",  # noqa: E501
    "test_ci_run_scope_event_field": "tests.core.test_types_protocols",
    "test_ci_run_scope_event_defaults_to_none": "tests.core.test_types_protocols",
    "test_skill_command_prefix_constant_exists": "tests.core.test_types_infrastructure_faults",
    "test_autoskillit_skill_prefix_constant_exists": "tests.core.test_types_infrastructure_faults",
    "test_write_expected_skills_frozenset_removed": "tests.core.test_types_infrastructure_faults",
    "test_write_behavior_spec_dataclass": "tests.core.test_types_infrastructure_faults",
    "test_infrastructure_fault_exceptions_share_marker_base": "tests.core.test_types_infrastructure_faults",  # noqa: E501
    # test_type_constants.py → 9 files
    "test_response_backstop_exemption_def_namedtuple_fields": "tests.core.test_type_constants_response_backstop",  # noqa: E501
    "test_response_backstop_exemption_registry_is_closed_and_pinned": "tests.core.test_type_constants_response_backstop",  # noqa: E501
    "test_response_backstop_exemption_registry_digest_is_canonical": "tests.core.test_type_constants_response_backstop",  # noqa: E501
    "test_response_backstop_exemption_registry_public_gateways": "tests.core.test_type_constants_response_backstop",  # noqa: E501
    "test_recipe_execution_install_site_registry_digest_is_canonical": "tests.core.test_type_constants_response_backstop",  # noqa: E501
    "test_core_packs_constant_defined": "tests.core.test_type_constants_pack_registry",
    "test_pack_registry_contains_all_packs": "tests.core.test_type_constants_pack_registry",
    "test_category_tags_derived_from_pack_registry": "tests.core.test_type_constants_pack_registry",  # noqa: E501
    "test_pack_registry_is_superset_of_old_category_tags": "tests.core.test_type_constants_pack_registry",  # noqa: E501
    "test_pack_def_namedtuple_fields": "tests.core.test_type_constants_pack_registry",
    "test_pack_registry_new_packs_are_default_disabled": "tests.core.test_type_constants_pack_registry",  # noqa: E501
    "test_audit_pipeline_pack_in_registry": "tests.core.test_type_constants_pack_registry",
    "test_pack_registry_importable_from_core": "tests.core.test_type_constants_pack_registry",
    "test_kitchen_core_in_pack_registry": "tests.core.test_type_constants_pack_registry",
    "test_private_env_vars_includes_franchise_tier_vars": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_private_env_vars_includes_execution_control_vars": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_private_env_vars_include_native_shell_lineage_controls": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_session_deadline_in_private_env_vars": "tests.core.test_type_constants_private_env_vars",
    "test_fleet_inspector_model_crosses_only_declared_child_boundaries": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_codex_mcp_receives_launch_registry_join": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_campaign_id_env_var_and_kitchen_session_id_env_var_exported_from_core": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_provider_profile_in_private_env_vars": "tests.core.test_type_constants_private_env_vars",
    "test_scenario_step_name_in_private_env_vars": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_autoskillit_allowed_write_prefix_in_private_env_vars": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_autoskillit_allowed_write_prefixes_in_private_env_vars": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_max_mcp_output_tokens_in_private_env_vars": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_autoskillit_cwd_in_private_env_vars": "tests.core.test_type_constants_private_env_vars",
    "test_autoskillit_write_guard_tool_names_in_private_env_vars": "tests.core.test_type_constants_private_env_vars",  # noqa: E501
    "test_recipe_pack_def_namedtuple_fields": "tests.core.test_type_constants_recipe_pack",
    "test_recipe_pack_registry_has_three_entries": "tests.core.test_type_constants_recipe_pack",
    "test_recipe_pack_registry_implementation_family": "tests.core.test_type_constants_recipe_pack",  # noqa: E501
    "test_recipe_pack_registry_research_family": "tests.core.test_type_constants_recipe_pack",
    "test_recipe_pack_registry_orchestration_family": "tests.core.test_type_constants_recipe_pack",
    "test_recipe_pack_def_importable_from_core": "tests.core.test_type_constants_recipe_pack",
    "test_feature_reveal_tags_removed": "tests.core.test_type_constants_feature_registry",
    "test_exclusive_feature_tools_removed": "tests.core.test_type_constants_feature_registry",
    "test_exclusive_feature_tools_not_in_all": "tests.core.test_type_constants_feature_registry",
    "test_fleet_default_enabled_is_false": "tests.core.test_type_constants_feature_registry",
    "test_exploration_feature_definition_pins_loading_and_visibility_policy": "tests.core.test_type_constants_feature_registry",  # noqa: E501
    "test_is_feature_enabled_fleet_defaults_false": "tests.core.test_type_constants_feature_registry",  # noqa: E501
    "test_feature_def_has_no_name_field": "tests.core.test_type_constants_feature_registry",
    "test_fleet_dispatch_tools_constant_exists": "tests.core.test_type_constants_fleet_tools",
    "test_fleet_menu_tools_in_type_constants": "tests.core.test_type_constants_fleet_tools",
    "test_fleet_menu_tools_not_in_fleet_init": "tests.core.test_type_constants_fleet_tools",
    "test_fleet_tools_matches_expected": "tests.core.test_type_constants_fleet_tools",
    "test_skill_tools_matches_expected": "tests.core.test_type_constants_fleet_tools",
    "test_config_authority_keys_constant": "tests.core.test_type_constants_fleet_tools",
    "test_headless_tools_contains_expected_names": "tests.core.test_type_constants_tool_classification",  # noqa: E501
    "test_evidence_reader_tools_are_exact_internal_subset": "tests.core.test_type_constants_tool_classification",  # noqa: E501
    "test_free_range_tools_contains_expected_names": "tests.core.test_type_constants_tool_classification",  # noqa: E501
    "test_session_type_cook_order_not_in_core_types": "tests.core.test_type_constants_env",
    "test_codex_schema_version_value": "tests.core.test_type_constants_env",
    "test_codex_schema_version_in_all": "tests.core.test_type_constants_env",
    "test_codex_schema_version_importable_from_types": "tests.core.test_type_constants_env",
    "test_codex_schema_version_importable_from_core": "tests.core.test_type_constants_env",
    "test_claude_code_mcp_tool_idle_timeout_env_var_value": "tests.core.test_type_constants_env",
    "test_claude_code_mcp_tool_idle_timeout_env_var_in_all": "tests.core.test_type_constants_env",
    "test_claude_code_mcp_tool_idle_timeout_env_var_importable_from_types": "tests.core.test_type_constants_env",  # noqa: E501
    "test_claude_code_mcp_tool_idle_timeout_env_var_importable_from_core": "tests.core.test_type_constants_env",  # noqa: E501
    "test_headless_auto_gate_env_var_value": "tests.core.test_type_constants_env",
    "test_headless_auto_gate_env_var_in_all": "tests.core.test_type_constants_env",
    "test_headless_auto_gate_env_var_importable_from_types": "tests.core.test_type_constants_env",
    "test_headless_auto_gate_env_var_importable_from_core": "tests.core.test_type_constants_env",
    "test_headless_auto_gate_env_var_in_private_env_vars": "tests.core.test_type_constants_env",
    "test_order_interactive_required_env_value": "tests.core.test_type_constants_env",
    "test_order_interactive_required_env_excludes_headless": "tests.core.test_type_constants_env",
    "test_order_interactive_required_env_in_all": "tests.core.test_type_constants_env",
    "test_order_interactive_required_env_importable_from_types": "tests.core.test_type_constants_env",  # noqa: E501
    "test_order_interactive_required_env_importable_from_core": "tests.core.test_type_constants_env",  # noqa: E501
    "test_codex_interactive_required_env_includes_max_mcp_output_tokens": "tests.core.test_type_constants_env",  # noqa: E501
    "test_codex_cook_storage_and_environment_constants_are_pinned": "tests.core.test_type_constants_env",  # noqa: E501
    "test_quota_trigger_constants_exported": "tests.core.test_type_constants_misc_markers",
    "test_investigation_complete_marker_defined": "tests.core.test_type_constants_misc_markers",
    "test_investigation_complete_marker_in_all": "tests.core.test_type_constants_misc_markers",
}

_SPLIT_TARGET_FILE_PATHS: tuple[str, ...] = (
    "tests/core/test_types_enums.py",
    "tests/core/test_types_skill_result.py",
    "tests/core/test_types_protocols.py",
    "tests/core/test_types_infrastructure_faults.py",
    "tests/core/test_type_constants_response_backstop.py",
    "tests/core/test_type_constants_pack_registry.py",
    "tests/core/test_type_constants_private_env_vars.py",
    "tests/core/test_type_constants_recipe_pack.py",
    "tests/core/test_type_constants_feature_registry.py",
    "tests/core/test_type_constants_fleet_tools.py",
    "tests/core/test_type_constants_tool_classification.py",
    "tests/core/test_type_constants_env.py",
    "tests/core/test_type_constants_misc_markers.py",
)


# ---------------------------------------------------------------------------
# Completeness guard tests
# ---------------------------------------------------------------------------


def test_pre_split_test_types_inventory_is_frozen() -> None:
    """The pre-split inventory must be a frozen set with no leading-dot or duplicate names."""
    assert isinstance(_PRE_SPLIT_TEST_TYPES_NAMES, frozenset)
    assert len(_PRE_SPLIT_TEST_TYPES_NAMES) == 44
    for name in _PRE_SPLIT_TEST_TYPES_NAMES:
        assert "." not in name, f"Invalid name with dot: {name}"


def test_pre_split_test_type_constants_inventory_is_frozen() -> None:
    """The pre-split inventory must be a frozen set with no leading-dot or duplicate names."""
    assert isinstance(_PRE_SPLIT_TEST_TYPE_CONSTANTS_NAMES, frozenset)
    assert len(_PRE_SPLIT_TEST_TYPE_CONSTANTS_NAMES) == 74
    for name in _PRE_SPLIT_TEST_TYPE_CONSTANTS_NAMES:
        assert "." not in name, f"Invalid name with dot: {name}"


def test_no_pre_split_test_name_appears_in_old_file() -> None:
    """The pre-split source files MUST NOT exist after the split."""
    repo_root = Path(__file__).resolve().parents[2]
    old_files = [
        repo_root / "tests" / "core" / "test_types.py",
        repo_root / "tests" / "core" / "test_type_constants.py",
    ]
    for old in old_files:
        assert not old.exists(), f"{old} still exists — Step 4 (delete source files) not completed"


def test_every_split_target_file_exists() -> None:
    """Every split target file path MUST exist after the split."""
    repo_root = Path(__file__).resolve().parents[2]
    for rel_path in _SPLIT_TARGET_FILE_PATHS:
        absolute = repo_root / rel_path
        assert absolute.exists(), f"{absolute} does not exist"


def test_pre_split_test_name_resolves_to_its_target_file() -> None:
    """For each pre-split test, the target module must import and the attribute must exist."""
    _bootstrap_tests_on_sys_path()

    missing: list[str] = []
    for test_name, target_module_path in _SPLIT_TARGETS.items():
        try:
            module = importlib.import_module(target_module_path)
        except ImportError as exc:
            missing.append(f"{test_name} → {target_module_path}: ImportError({exc})")
            continue
        if not _module_defines_name(module, test_name):
            missing.append(f"{test_name} → {target_module_path}: missing attribute")
    assert not missing, "Pre-split tests missing from target files:\n" + "\n".join(missing)


def test_every_pre_split_test_name_appears_in_exactly_one_new_file() -> None:
    """Each pre-split test name must appear in exactly one of the split files (no duplicates)."""
    all_pre_split: frozenset[str] = (
        _PRE_SPLIT_TEST_TYPES_NAMES | _PRE_SPLIT_TEST_TYPE_CONSTANTS_NAMES
    )

    counter: Counter[str] = Counter()
    for rel_path in _SPLIT_TARGET_FILE_PATHS:
        module_name = "tests.core." + Path(rel_path).stem
        module = importlib.import_module(module_name)
        for name in all_pre_split:
            if _module_defines_name(module, name):
                counter[name] += 1

    duplicates = {name: count for name, count in counter.items() if count != 1}
    missing = all_pre_split - set(counter)
    problems: list[str] = []
    if duplicates:
        problems.append(f"Pre-split names appearing in !=1 file: {sorted(duplicates.items())}")
    if missing:
        problems.append(f"Pre-split names appearing in 0 files: {missing}")
    assert not problems, "\n".join(problems)


def test_no_unintended_new_test_files_under_tests_core() -> None:
    """tests/core/ test_*.py file set must match the expected layout."""
    repo_root = Path(__file__).resolve().parents[2]
    tests_core = repo_root / "tests" / "core"
    actual_test_files = sorted(p.name for p in tests_core.glob("test_*.py"))
    expected = sorted(
        [
            # Pre-existing files (preserved)
            "test_add_dir_validation.py",
            "test_agent_definition.py",
            "test_append_only_store_bounds.py",
            "test_artifact_lease.py",
            "test_audit_admission_contracts.py",
            "test_audit_admission_ledger_contracts.py",
            "test_audit_cycle_attacks.py",
            "test_audit_cycle_authority.py",
            "test_audit_semantic_codec.py",
            "test_backend_capabilities.py",
            "test_backend_dataclasses.py",
            "test_backend_event_kind.py",
            "test_backend_gating_core.py",
            "test_backend_protocols.py",
            "test_bash_write_targets.py",
            "test_branch_guard.py",
            "test_build_agent_env.py",
            "test_canonical_token_usage.py",
            "test_capacity_fault.py",
            "test_capture.py",
            "test_child_env_bus_address.py",
            "test_claude_env.py",
            "test_client_serialized_char_len.py",
            "test_closure_attacks.py",
            "test_closure_hashing.py",
            "test_closure_verifier.py",
            "test_cmd_runner.py",
            "test_codex_cli_version.py",
            "test_context_admission_coverage.py",
            "test_context_admission_reducer.py",
            "test_context_admission_state_machine.py",
            "test_core.py",
            "test_core_terminal_table.py",
            "test_detect_body_marker.py",
            "test_directory_tree_digest.py",
            "test_ensure_project_temp_with_config.py",
            "test_entrypoint_shim.py",
            "test_executable_launch_binding.py",
            "test_exploration_contract_validation.py",
            "test_exploration_failure_classification.py",
            "test_feature_flags.py",
            "test_fs_observation.py",
            "test_git_remote.py",
            "test_github_review_ledger_path.py",
            "test_github_url.py",
            "test_grab_bag_split_completeness.py",
            "test_host_attestation_decision.py",
            "test_import_isolation.py",
            "test_input_spec_type.py",
            "test_inspector_types.py",
            "test_install_binding_seal_regression.py",
            "test_install_detect.py",
            "test_invariant_registry.py",
            "test_inventory_admission.py",
            "test_io.py",
            "test_io_spill.py",
            "test_json.py",
            "test_kitchen_state.py",
            "test_label_lifecycle.py",
            "test_logging.py",
            "test_parse_plan_paths.py",
            "test_path_containment.py",
            "test_paths.py",
            "test_pipeline_tracker.py",
            "test_plugin_artifact_identity.py",
            "test_plugin_cache.py",
            "test_process_cleanup_result.py",
            "test_recipe_delivery_contract.py",
            "test_recipe_execution_credential.py",
            "test_recipe_section_bound_resolver.py",
            "test_resolve_main_worktree.py",
            "test_resolve_temp_dir.py",
            "test_session_checkpoint.py",
            "test_session_env_specs.py",
            "test_session_index_schema.py",
            "test_session_liveness.py",
            "test_session_provenance.py",
            "test_session_registry.py",
            "test_session_type.py",
            "test_skill_command_parsing.py",
            "test_skill_contract_types.py",
            "test_skill_semantic_plan.py",
            "test_stamp_constants.py",
            "test_tool_sequence_analysis.py",
            "test_type_helpers.py",
            "test_type_protocol_shards.py",
            "test_type_results_execution.py",
            "test_type_results_records.py",
            "test_typed_dimensional_bounds.py",
            "test_unit_mixing_rejection.py",
            "test_validated_worktree_path.py",
            "test_version_snapshot.py",
            "test_version_snapshot_codex_routing.py",
            # 13 new split files
            "test_types_enums.py",
            "test_types_infrastructure_faults.py",
            "test_types_protocols.py",
            "test_types_skill_result.py",
            "test_type_constants_env.py",
            "test_type_constants_feature_registry.py",
            "test_type_constants_fleet_tools.py",
            "test_type_constants_misc_markers.py",
            "test_type_constants_pack_registry.py",
            "test_type_constants_private_env_vars.py",
            "test_type_constants_recipe_pack.py",
            "test_type_constants_response_backstop.py",
            "test_type_constants_tool_classification.py",
            "test_types_phoropter.py",
            "test_types_structure.py",
        ]
    )
    assert actual_test_files == expected, (
        f"File layout drift.\n"
        f"Missing from tests/core/: {sorted(set(expected) - set(actual_test_files))}\n"
        f"Unexpected in tests/core/: {sorted(set(actual_test_files) - set(expected))}"
    )


# ---------------------------------------------------------------------------
# Convention-conformance tests
# ---------------------------------------------------------------------------


def test_every_split_file_has_layer_core_marker() -> None:
    """Each split file's module-level pytestmark must include pytest.mark.layer('core')."""
    _bootstrap_tests_on_sys_path()

    for rel_path in _SPLIT_TARGET_FILE_PATHS:
        module_name = "tests.core." + Path(rel_path).stem
        module = importlib.import_module(module_name)
        markers = getattr(module, "pytestmark", [])
        marker_list = markers if isinstance(markers, list) else [markers]
        layer_marker = next(
            (m for m in marker_list if m.name == "layer"),
            None,
        )
        assert layer_marker is not None, f"{rel_path} has no layer() marker"
        assert layer_marker.args and layer_marker.args[0] == "core", (
            f"{rel_path} layer() marker is not 'core' (got {layer_marker.args})"
        )


def test_every_split_file_has_size_marker() -> None:
    """Each split file must declare a size marker (small or medium)."""
    _bootstrap_tests_on_sys_path()

    for rel_path in _SPLIT_TARGET_FILE_PATHS:
        module_name = "tests.core." + Path(rel_path).stem
        module = importlib.import_module(module_name)
        markers = getattr(module, "pytestmark", [])
        marker_list = markers if isinstance(markers, list) else [markers]
        size_marker = next(
            (m for m in marker_list if m.name in {"small", "medium", "large"}),
            None,
        )
        assert size_marker is not None, f"{rel_path} has no size marker (small/medium/large)"


def test_no_split_file_imports_autoskillit_execution() -> None:
    """Split files must not import autoskillit.execution — the legacy allowlist entry
    in tests/arch/test_layer_enforcement.py is removed in Step 3.1."""
    repo_root = Path(__file__).resolve().parents[2]
    for rel_path in _SPLIT_TARGET_FILE_PATHS:
        source = (repo_root / rel_path).read_text(encoding="utf-8")
        assert not _source_imports_autoskillit_execution(source), (
            f"{rel_path} still imports autoskillit.execution — "
            f"remove the import or update _TEST_LAYER_ALLOWLIST"
        )


def test_split_files_do_not_redeclare_conftest_autouse_fixture() -> None:
    """conftest.py provides _clear_snapshot_cache autouse; no split file may shadow it."""
    repo_root = Path(__file__).resolve().parents[2]
    for rel_path in _SPLIT_TARGET_FILE_PATHS:
        source = (repo_root / rel_path).read_text(encoding="utf-8")
        assert not _source_defines_clear_snapshot_cache(source), (
            f"{rel_path} shadows the conftest-provided _clear_snapshot_cache fixture"
        )


def test_layer_enforcement_allowlist_no_longer_references_deleted_file() -> None:
    """The split removes the historical autoskillit.execution allowlist entry for test_types.py."""
    from tests.arch.test_layer_enforcement import _TEST_LAYER_ALLOWLIST

    assert "tests/core/test_types.py" not in _TEST_LAYER_ALLOWLIST  # type: ignore[attr-defined]
    assert "tests/core/test_type_constants.py" not in _TEST_LAYER_ALLOWLIST  # type: ignore[attr-defined]
