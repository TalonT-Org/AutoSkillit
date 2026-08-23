"""Private construction helpers for canonical MCP tool definitions."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .types._type_recipe_binding import (
    ToolDef,
    ToolInitializationOperation,
    ToolParamDef,
    ToolParamRole,
    ToolWireType,
)

_INSPECTION_TOOLS = frozenset(
    {
        "analyze_tool_sequences",
        "check_pr_mergeable",
        "check_repo_merge_state",
        "fetch_github_issue",
        "get_authorized_artifact_page",
        "get_ci_status",
        "get_exploration_page",
        "get_issue_title",
        "inspect_session_logs",
        "get_pipeline_report",
        "get_pr_reviews",
        "get_quota_events",
        "get_timing_summary",
        "get_token_summary",
        "kitchen_status",
        "list_recipes",
        "load_recipe",
        "read_authorized_artifact",
        "read_db",
        "resume_exploration_context",
        "submit_exploration_query",
        "validate_recipe",
    }
)
_LIFECYCLE_CONTROL_TOOLS = frozenset({"close_kitchen", "open_kitchen"})
_RECOVERY_TOOLS = frozenset(
    {
        "complete_recipe_initialization",
        "complete_run_skill_result",
        "get_recipe_section",
        "recover_run_skill_result",
    }
)
_EXECUTION_TOOLS = frozenset(
    {"delegate_evidence_reader", "run_cmd", "run_python", "run_skill", "test_check"}
)
_MUTATION_TOOLS = frozenset(
    {
        "batch_cleanup_clones",
        "bootstrap_clone",
        "bulk_close_issues",
        "claim_and_resolve_issue",
        "claim_issue",
        "classify_fix",
        "clone_repo",
        "commit_files",
        "configure_fleet",
        "configure_order",
        "create_and_publish_branch",
        "create_unique_branch",
        "declare_join_batch",
        "disable_quota_guard",
        "dispatch_food_truck",
        "enable_exploration",
        "enqueue_pr",
        "lock_ingredients",
        "merge_worktree",
        "migrate_recipe",
        "prepare_issue",
        "post_pr_review",
        "push_to_remote",
        "record_gate_dispatch",
        "record_pipeline_step",
        "register_clone_status",
        "release_issue",
        "reload_session",
        "remove_clone",
        "report_bug",
        "reset_dispatch",
        "reset_test_dir",
        "reset_workspace",
        "set_commit_status",
        "toggle_auto_merge",
        "unlock_agent_pack",
        "wait_for_ci",
        "wait_for_merge_queue",
        "write_audit_disposition_bundle",
        "write_audit_semantic_result",
        "write_standalone_audit_evidence",
        "write_telemetry_files",
    }
)


def _initialization_operation(name: str) -> ToolInitializationOperation:
    if name in _RECOVERY_TOOLS:
        return ToolInitializationOperation.RECOVERY
    if name in _INSPECTION_TOOLS:
        return ToolInitializationOperation.INSPECTION
    if name in _LIFECYCLE_CONTROL_TOOLS:
        return ToolInitializationOperation.LIFECYCLE_CONTROL
    if name in _EXECUTION_TOOLS:
        return ToolInitializationOperation.EXECUTION
    if name in _MUTATION_TOOLS:
        return ToolInitializationOperation.MUTATION
    raise ValueError(f"Tool {name!r} has no initialization-time operation class")


def _tool(
    name: str,
    params: tuple[str, ...] = (),
    *,
    required: tuple[str, ...] = (),
    wire_types: Mapping[str, ToolWireType] | None = None,
    roles: Mapping[str, ToolParamRole] | None = None,
    automatic_recipe_delivery: bool = False,
    recovery_recipe_delivery: bool = False,
) -> ToolDef:
    required_set = frozenset(required)
    declared_wire_types = wire_types or {}
    declared_roles = roles or {}
    unknown_role_params = set(declared_roles) - set(params)
    if unknown_role_params:
        raise ValueError(
            f"Tool {name!r} declares roles for unknown parameter(s): {sorted(unknown_role_params)}"
        )
    return ToolDef(
        name=name,
        params=tuple(
            ToolParamDef(
                param,
                wire_type=declared_wire_types.get(param, ToolWireType.SCALAR),
                required=param in required_set,
                role=declared_roles.get(param, ToolParamRole.CHILD_INPUT),
            )
            for param in params
        ),
        initialization_operation=_initialization_operation(name),
        automatic_recipe_delivery=automatic_recipe_delivery,
        recovery_recipe_delivery=recovery_recipe_delivery,
    )


# The single classification authority for what each run_skill parameter is
# for. Every attestation-relevant surface (the runtime gate's always-admit
# set, the actual-kwargs assembly, the execution-tuning fallback table, and
# the frozen ledger in tests/contracts/test_run_skill_kwarg_ledger.py) is
# derived from this mapping — see ToolParamRole for what each role means.
_RUN_SKILL_PARAM_ROLES: Mapping[str, ToolParamRole] = MappingProxyType(
    {
        "skill_command": ToolParamRole.CHILD_INPUT,
        "cwd": ToolParamRole.CHILD_INPUT,
        "model": ToolParamRole.EXECUTION_TUNING,
        "step_name": ToolParamRole.PROTOCOL,
        "recipe_execution_id": ToolParamRole.PROTOCOL,
        "invocation_template_digest": ToolParamRole.PROTOCOL,
        "step_provider": ToolParamRole.EXECUTION_TUNING,
        "order_id": ToolParamRole.ORCHESTRATOR_SCOPING,
        "stale_threshold": ToolParamRole.EXECUTION_TUNING,
        "idle_output_timeout": ToolParamRole.EXECUTION_TUNING,
        "output_dir": ToolParamRole.CHILD_INPUT,
        "resume_session_id": ToolParamRole.SESSION_FLOW,
        "retry_after_audit_attempt_id": ToolParamRole.SESSION_FLOW,
        "native_shell_capture_mode": ToolParamRole.SESSION_FLOW,
        "closure_authority_path": ToolParamRole.SESSION_FLOW,
        "closure_authority_hash": ToolParamRole.SESSION_FLOW,
        "closure_plan_paths": ToolParamRole.SESSION_FLOW,
        "closure_base_sha": ToolParamRole.SESSION_FLOW,
        "closure_diff_sha": ToolParamRole.SESSION_FLOW,
        "closure_target_sha": ToolParamRole.SESSION_FLOW,
        "dispatch_items": ToolParamRole.SESSION_FLOW,
        "skill_inputs": ToolParamRole.CHILD_INPUT,
    }
)


def _run_skill() -> ToolDef:
    string_params = (
        "skill_command",
        "cwd",
        "model",
        "step_name",
        "recipe_execution_id",
        "invocation_template_digest",
        "step_provider",
        "order_id",
        "output_dir",
        "resume_session_id",
        "retry_after_audit_attempt_id",
        "native_shell_capture_mode",
        "closure_authority_path",
        "closure_authority_hash",
        "closure_plan_paths",
        "closure_base_sha",
        "closure_diff_sha",
        "closure_target_sha",
    )
    params = [
        ToolParamDef(
            name,
            wire_type=ToolWireType.STRING,
            required=name in {"skill_command", "cwd"},
            role=_RUN_SKILL_PARAM_ROLES[name],
        )
        for name in string_params
    ]
    params[8:8] = [
        ToolParamDef(
            "stale_threshold",
            ToolWireType.INTEGER,
            role=_RUN_SKILL_PARAM_ROLES["stale_threshold"],
        ),
        ToolParamDef(
            "idle_output_timeout",
            ToolWireType.INTEGER,
            role=_RUN_SKILL_PARAM_ROLES["idle_output_timeout"],
        ),
    ]
    params.append(
        ToolParamDef(
            "dispatch_items",
            ToolWireType.STRING,
            handler_parameter=False,
            role=_RUN_SKILL_PARAM_ROLES["dispatch_items"],
        )
    )
    params.append(
        ToolParamDef(
            "skill_inputs",
            ToolWireType.OBJECT,
            structured_skill_inputs=True,
            handler_parameter=True,
            role=_RUN_SKILL_PARAM_ROLES["skill_inputs"],
        )
    )
    return ToolDef(
        name="run_skill",
        params=tuple(params),
        initialization_operation=_initialization_operation("run_skill"),
    )
