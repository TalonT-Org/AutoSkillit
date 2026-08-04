"""Canonical stdlib-only MCP tool metadata.

Handler parameters mirror the public ``@mcp.tool`` signatures.  ``skill_inputs``
is the sole compiler-owned structured recipe parameter; the next server phase
will expose that already-compiled channel on ``run_skill``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .closure_hashing import compute_canonical_hash
from .types._type_constants_registries import HEADLESS_TOOLS
from .types._type_recipe_binding import (
    ToolDef,
    ToolInitializationOperation,
    ToolParamDef,
    ToolWireType,
)

__all__ = [
    "TOOL_REGISTRY",
    "all_tool_names",
    "compute_tool_contract_identity",
    "get_tool_def",
    "unsupported_tool_params",
]

_TOOL_CONTRACT_IDENTITY_DOMAIN = "autoskillit:tool-contract:v1:sha256"

_INSPECTION_TOOLS = frozenset(
    {
        "analyze_tool_sequences",
        "check_pr_mergeable",
        "check_repo_merge_state",
        "fetch_github_issue",
        "get_ci_status",
        "get_exploration_page",
        "get_issue_title",
        "get_pipeline_report",
        "get_pr_reviews",
        "get_quota_events",
        "get_timing_summary",
        "get_token_summary",
        "kitchen_status",
        "list_recipes",
        "load_recipe",
        "read_db",
        "resume_exploration_context",
        "submit_exploration_query",
        "validate_recipe",
    }
)
_LIFECYCLE_CONTROL_TOOLS = frozenset({"close_kitchen", "open_kitchen"})
_RECOVERY_TOOLS = frozenset({"complete_recipe_initialization", "get_recipe_section"})
_EXECUTION_TOOLS = frozenset({"run_cmd", "run_python", "run_skill", "test_check"})
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
        "disable_quota_guard",
        "dispatch_food_truck",
        "enqueue_pr",
        "enrich_issues",
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
) -> ToolDef:
    required_set = frozenset(required)
    declared_wire_types = wire_types or {}
    return ToolDef(
        name=name,
        params=tuple(
            ToolParamDef(
                param,
                wire_type=declared_wire_types.get(param, ToolWireType.SCALAR),
                required=param in required_set,
            )
            for param in params
        ),
        initialization_operation=_initialization_operation(name),
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
        )
        for name in string_params
    ]
    params[8:8] = [
        ToolParamDef("stale_threshold", ToolWireType.INTEGER),
        ToolParamDef("idle_output_timeout", ToolWireType.INTEGER),
    ]
    params.append(
        ToolParamDef(
            "dispatch_items",
            ToolWireType.STRING,
            handler_parameter=False,
        )
    )
    params.append(
        ToolParamDef(
            "skill_inputs",
            ToolWireType.OBJECT,
            structured_skill_inputs=True,
            handler_parameter=True,
        )
    )
    return ToolDef(
        name="run_skill",
        params=tuple(params),
        initialization_operation=_initialization_operation("run_skill"),
    )


_TOOL_DEFS = (
    _tool("unlock_agent_pack", ("pack_name",), required=("pack_name",)),
    _tool(
        "set_commit_status",
        ("sha", "state", "context", "description", "target_url", "repo", "cwd"),
        required=("sha", "state", "context"),
    ),
    _tool(
        "check_repo_merge_state",
        ("branch", "cwd", "remote_url", "step_name", "base_branch"),
        required=("branch",),
    ),
    _tool(
        "toggle_auto_merge",
        ("pr_number", "target_branch", "cwd", "repo", "remote_url"),
        required=("pr_number", "target_branch", "cwd"),
    ),
    _tool(
        "enqueue_pr",
        (
            "pr_number",
            "target_branch",
            "cwd",
            "auto_merge_available",
            "repo",
            "remote_url",
            "step_name",
        ),
        required=("pr_number", "target_branch", "cwd", "auto_merge_available"),
    ),
    _tool(
        "wait_for_merge_queue",
        (
            "pr_number",
            "target_branch",
            "cwd",
            "repo",
            "remote_url",
            "timeout_seconds",
            "poll_interval",
            "stall_grace_period",
            "max_stall_retries",
            "not_in_queue_confirmation_cycles",
            "max_inconclusive_retries",
            "auto_merge_available",
            "max_merge_group_drops",
            "merge_group_drop_backoff",
            "step_name",
        ),
        required=("pr_number", "target_branch", "cwd"),
    ),
    _tool(
        "wait_for_ci",
        (
            "branch",
            "repo",
            "remote_url",
            "head_sha",
            "workflow",
            "event",
            "timeout_seconds",
            "lookback_seconds",
            "cwd",
            "step_name",
            "auto_trigger",
        ),
        required=("branch",),
    ),
    _tool("get_ci_status", ("branch", "run_id", "repo", "workflow", "event", "cwd")),
    _tool(
        "submit_exploration_query",
        ("query", "max_results"),
        required=("query",),
        wire_types={"max_results": ToolWireType.INTEGER},
    ),
    _tool(
        "get_exploration_page",
        ("page_size", "continuation"),
        wire_types={"page_size": ToolWireType.INTEGER},
    ),
    _tool(
        "resume_exploration_context",
        ("page_size",),
        wire_types={"page_size": ToolWireType.INTEGER},
    ),
    _tool(
        "clone_repo",
        ("source_dir", "run_name", "branch", "strategy", "remote_url", "step_name"),
        required=("source_dir", "run_name"),
    ),
    _tool("remove_clone", ("clone_path", "keep", "step_name"), required=("clone_path",)),
    _tool(
        "push_to_remote",
        ("clone_path", "branch", "source_dir", "remote_url", "force", "step_name"),
        required=("clone_path", "branch"),
    ),
    _tool(
        "register_clone_status",
        ("clone_path", "status", "registry_path", "step_name"),
        required=("clone_path", "status"),
    ),
    _tool(
        "batch_cleanup_clones",
        ("registry_path", "all_owners", "owner_filter", "step_name"),
    ),
    _tool(
        "bootstrap_clone",
        ("source_dir", "run_name", "base_branch", "branch", "strategy", "remote_url", "step_name"),
        required=("source_dir", "run_name", "base_branch"),
    ),
    _tool(
        "configure_fleet",
        (
            "max_concurrent_dispatches",
            "max_total_issues",
            "default_timeout_sec",
            "max_extension_seconds",
            "idle_output_timeout",
            "acquire_timeout_sec",
            "max_issues_per_food_truck",
            "enable_deadline_extension",
            "inspector_model",
            "default_model",
        ),
        wire_types={
            "max_concurrent_dispatches": ToolWireType.INTEGER,
            "max_total_issues": ToolWireType.INTEGER,
            "default_timeout_sec": ToolWireType.INTEGER,
            "max_extension_seconds": ToolWireType.INTEGER,
            "idle_output_timeout": ToolWireType.INTEGER,
            "acquire_timeout_sec": ToolWireType.INTEGER,
            "max_issues_per_food_truck": ToolWireType.INTEGER,
            "enable_deadline_extension": ToolWireType.BOOLEAN,
        },
    ),
    _tool(
        "configure_order",
        (
            "timeout",
            "stale_threshold",
            "idle_output_timeout",
            "max_suppression_seconds",
            "default_model",
        ),
        wire_types={
            "timeout": ToolWireType.INTEGER,
            "stale_threshold": ToolWireType.INTEGER,
            "idle_output_timeout": ToolWireType.INTEGER,
            "max_suppression_seconds": ToolWireType.INTEGER,
        },
    ),
    _tool("run_cmd", ("cmd", "cwd", "timeout", "step_name"), required=("cmd", "cwd")),
    _tool(
        "run_python",
        ("callable", "args", "timeout", "work_dir"),
        required=("callable",),
        wire_types={"args": ToolWireType.OBJECT},
    ),
    _run_skill(),
    _tool(
        "dispatch_food_truck",
        (
            "recipe",
            "task",
            "ingredients",
            "dispatch_name",
            "timeout_sec",
            "capture",
            "resume_session_id",
            "resume_checkpoint",
            "idle_output_timeout",
            "prior_dispatch_id",
            "skip_when",
            "resume_message",
            "caller_instructions",
            "backend",
            "native_shell_capture_mode",
        ),
        required=("recipe", "task"),
        wire_types={
            "ingredients": ToolWireType.OBJECT,
            "capture": ToolWireType.OBJECT,
            "resume_checkpoint": ToolWireType.OBJECT,
            "native_shell_capture_mode": ToolWireType.STRING,
        },
    ),
    _tool(
        "record_gate_dispatch",
        ("dispatch_name", "approved"),
        required=("dispatch_name", "approved"),
        wire_types={"approved": ToolWireType.BOOLEAN},
    ),
    _tool(
        "reset_dispatch",
        ("dispatch_id", "reset_to", "force", "destroy_artifacts"),
        required=("dispatch_id",),
        wire_types={
            "force": ToolWireType.BOOLEAN,
            "destroy_artifacts": ToolWireType.BOOLEAN,
        },
    ),
    _tool(
        "merge_worktree",
        ("worktree_path", "base_branch", "step_name"),
        required=("worktree_path", "base_branch"),
    ),
    _tool(
        "classify_fix",
        ("worktree_path", "base_branch", "step_name"),
        required=("worktree_path", "base_branch"),
    ),
    _tool(
        "create_unique_branch",
        ("slug", "issue_number", "remote", "cwd", "base_branch_name", "step_name"),
    ),
    _tool("check_pr_mergeable", ("pr_number", "cwd", "repo"), required=("pr_number", "cwd")),
    _tool(
        "create_and_publish_branch",
        ("issue_slug", "run_name", "issue_number", "work_dir", "remote_url", "step_name"),
        required=("issue_slug", "run_name", "issue_number", "work_dir", "remote_url"),
    ),
    _tool(
        "commit_files",
        ("paths", "message", "cwd", "step_name"),
        required=("paths", "message", "cwd"),
    ),
    _tool(
        "write_audit_semantic_result",
        (
            "reservation_handle",
            "audited_plan_refs",
            "assessments",
            "verdict",
            "remediation_ref",
            "step_name",
        ),
        required=("reservation_handle", "audited_plan_refs", "assessments", "verdict"),
        wire_types={
            "audited_plan_refs": ToolWireType.ARRAY,
            "assessments": ToolWireType.ARRAY,
            "remediation_ref": ToolWireType.OBJECT,
        },
    ),
    _tool(
        "write_standalone_audit_evidence",
        (
            "audited_plan_refs",
            "assessments",
            "verdict",
            "remediation_ref",
            "step_name",
        ),
        required=("audited_plan_refs", "assessments", "verdict"),
        wire_types={
            "audited_plan_refs": ToolWireType.ARRAY,
            "assessments": ToolWireType.ARRAY,
            "remediation_ref": ToolWireType.OBJECT,
        },
    ),
    _tool(
        "write_audit_disposition_bundle",
        (
            "authority_path",
            "new_plan_path",
            "new_plan_media_type",
            "new_plan_schema_version",
            "dispositions",
            "step_name",
        ),
        required=(
            "authority_path",
            "new_plan_path",
            "new_plan_media_type",
            "new_plan_schema_version",
            "dispositions",
        ),
        wire_types={
            "new_plan_schema_version": ToolWireType.INTEGER,
            "dispositions": ToolWireType.ARRAY,
        },
    ),
    _tool(
        "fetch_github_issue",
        ("issue_url", "include_comments"),
        required=("issue_url",),
    ),
    _tool("get_issue_title", ("issue_url",), required=("issue_url",)),
    _tool(
        "report_bug",
        ("error_context", "cwd", "severity", "model", "step_name"),
        required=("error_context", "cwd"),
    ),
    _tool(
        "claim_and_resolve_issue",
        ("issue_url", "label", "allow_reentry"),
        required=("issue_url",),
    ),
    _tool(
        "prepare_issue",
        ("title", "body", "repo", "labels", "dry_run", "split"),
        required=("title", "body"),
    ),
    _tool("enrich_issues", ("issue_number", "batch", "dry_run", "repo")),
    _tool("claim_issue", ("issue_url", "label", "allow_reentry"), required=("issue_url",)),
    _tool(
        "release_issue",
        ("issue_url", "label", "target_branch", "staged_label", "fail_label", "close_issue"),
        required=("issue_url",),
    ),
    _tool(
        "open_kitchen",
        ("name", "overrides", "ingredients_only", "delivery_request"),
        wire_types={
            "overrides": ToolWireType.OBJECT,
            "ingredients_only": ToolWireType.BOOLEAN,
            "delivery_request": ToolWireType.OBJECT,
        },
    ),
    _tool("close_kitchen"),
    _tool("disable_quota_guard"),
    _tool("lock_ingredients", ("locked", "pipeline_id", "unlock")),
    _tool("reload_session"),
    _tool("record_pipeline_step", ("pipeline_id", "op", "dependencies", "step_name")),
    _tool("get_pr_reviews", ("pr_number", "cwd", "repo"), required=("pr_number", "cwd")),
    _tool(
        "post_pr_review",
        (
            "cwd",
            "receipt_path",
            "repository",
            "pr_number",
            "head_sha",
            "logical_iteration",
            "event",
            "body",
            "comments",
            "dry_run",
        ),
        required=(
            "cwd",
            "receipt_path",
            "repository",
            "pr_number",
            "head_sha",
            "logical_iteration",
            "event",
            "body",
            "comments",
            "dry_run",
        ),
        wire_types={
            "cwd": ToolWireType.STRING,
            "receipt_path": ToolWireType.STRING,
            "repository": ToolWireType.STRING,
            "pr_number": ToolWireType.INTEGER,
            "head_sha": ToolWireType.STRING,
            "logical_iteration": ToolWireType.STRING,
            "event": ToolWireType.STRING,
            "body": ToolWireType.STRING,
            "comments": ToolWireType.ARRAY,
            "dry_run": ToolWireType.BOOLEAN,
        },
    ),
    _tool(
        "bulk_close_issues",
        ("issue_numbers", "comment", "cwd"),
        required=("issue_numbers", "comment", "cwd"),
        wire_types={"issue_numbers": ToolWireType.ARRAY},
    ),
    _tool("list_recipes"),
    _tool(
        "load_recipe",
        ("name", "overrides", "ingredients_only", "delivery_request"),
        required=("name",),
        wire_types={
            "overrides": ToolWireType.OBJECT,
            "ingredients_only": ToolWireType.BOOLEAN,
            "delivery_request": ToolWireType.OBJECT,
        },
    ),
    _tool(
        "get_recipe_section",
        (
            "section",
            "recipe_name",
            "producer_tool",
            "descriptor_version",
            "schema_version",
            "payload_sha256",
            "artifact_blob_sha256",
            "artifact_blob_size_bytes",
            "body_sha256",
            "body_size_bytes",
            "flow_schema_version",
            "flow_sha256",
            "flow_size_bytes",
            "flow_record_count",
            "part",
            "initialization_id",
            "page_plan_sha256",
            "continuation",
        ),
        required=(
            "section",
            "recipe_name",
            "producer_tool",
            "descriptor_version",
            "schema_version",
            "payload_sha256",
            "artifact_blob_sha256",
            "artifact_blob_size_bytes",
            "body_sha256",
            "body_size_bytes",
            "flow_schema_version",
            "flow_sha256",
            "flow_size_bytes",
            "flow_record_count",
        ),
    ),
    _tool(
        "complete_recipe_initialization",
        ("initialization_id",),
        required=("initialization_id",),
        wire_types={"initialization_id": ToolWireType.STRING},
    ),
    _tool("validate_recipe", ("script_path",), required=("script_path",)),
    _tool("migrate_recipe", ("name",), required=("name",)),
    _tool("kitchen_status"),
    _tool("get_pipeline_report", ("clear",)),
    _tool("get_token_summary", ("clear", "format", "order_id")),
    _tool("get_timing_summary", ("clear", "format", "order_id")),
    _tool("analyze_tool_sequences", ("recipe", "format", "top_n", "min_count")),
    _tool("get_quota_events", ("n",)),
    _tool("write_telemetry_files", ("output_dir",), required=("output_dir",)),
    _tool(
        "read_db",
        ("db_path", "query", "params", "timeout"),
        required=("db_path", "query"),
        wire_types={"params": ToolWireType.ARRAY},
    ),
    _tool("test_check", ("worktree_path", "step_name"), required=("worktree_path",)),
    _tool("reset_test_dir", ("test_dir", "force", "step_name"), required=("test_dir",)),
    _tool("reset_workspace", ("test_dir",), required=("test_dir",)),
)


def _build_registry(tool_defs: tuple[ToolDef, ...]) -> Mapping[str, ToolDef]:
    registry: dict[str, ToolDef] = {}
    for tool_def in tool_defs:
        if tool_def.name in registry:
            raise ValueError(f"Duplicate canonical ToolDef: {tool_def.name!r}")
        registry[tool_def.name] = tool_def
    return MappingProxyType(registry)


TOOL_REGISTRY: Mapping[str, ToolDef] = _build_registry(_TOOL_DEFS)
if not HEADLESS_TOOLS <= TOOL_REGISTRY.keys():
    raise RuntimeError(
        "Canonical tool registry is missing headless tools: "
        f"{sorted(HEADLESS_TOOLS - TOOL_REGISTRY.keys())}"
    )


def get_tool_def(tool_name: str) -> ToolDef | None:
    return TOOL_REGISTRY.get(tool_name)


def compute_tool_contract_identity(tool_def: ToolDef) -> str:
    """Hash every canonical parameter property that defines a tool contract."""
    return compute_canonical_hash(
        {
            "name": tool_def.name,
            "initialization_operation": tool_def.initialization_operation.value,
            "params": [
                {
                    "handler_parameter": param.handler_parameter,
                    "name": param.name,
                    "required": param.required,
                    "structured_skill_inputs": param.structured_skill_inputs,
                    "wire_type": param.wire_type.value,
                }
                for param in tool_def.params
            ],
        },
        domain=_TOOL_CONTRACT_IDENTITY_DOMAIN,
    )


def all_tool_names() -> frozenset[str]:
    return frozenset(TOOL_REGISTRY)


def unsupported_tool_params(
    tool_name: str,
    params: Mapping[str, object] | frozenset[str] | set[str],
) -> frozenset[str]:
    tool_def = get_tool_def(tool_name)
    keys = frozenset(params)
    if tool_def is None:
        return keys
    return keys - tool_def.param_set
