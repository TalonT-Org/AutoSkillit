"""Canonical stdlib-only MCP tool metadata.

Handler parameters mirror the public ``@mcp.tool`` signatures.  ``skill_inputs``
is the sole compiler-owned structured recipe parameter; the next server phase
will expose that already-compiled channel on ``run_skill``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .types._type_recipe_binding import ToolDef, ToolParamDef, ToolWireType

__all__ = [
    "TOOL_REGISTRY",
    "all_tool_names",
    "get_tool_def",
    "unsupported_tool_params",
]


def _tool(
    name: str,
    params: tuple[str, ...] = (),
    *,
    required: tuple[str, ...] = (),
) -> ToolDef:
    required_set = frozenset(required)
    return ToolDef(
        name=name,
        params=tuple(ToolParamDef(param, required=param in required_set) for param in params),
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
            "skill_inputs",
            ToolWireType.OBJECT,
            structured_skill_inputs=True,
            handler_parameter=True,
        )
    )
    return ToolDef(name="run_skill", params=tuple(params))


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
    ),
    _tool("run_cmd", ("cmd", "cwd", "timeout", "step_name"), required=("cmd", "cwd")),
    _tool("run_python", ("callable", "args", "timeout", "work_dir"), required=("callable",)),
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
        ),
        required=("recipe", "task"),
    ),
    _tool(
        "record_gate_dispatch",
        ("dispatch_name", "approved"),
        required=("dispatch_name", "approved"),
    ),
    _tool(
        "reset_dispatch",
        ("dispatch_id", "reset_to", "force", "destroy_artifacts"),
        required=("dispatch_id",),
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
    _tool("open_kitchen", ("name", "overrides", "ingredients_only", "delivery_request")),
    _tool("close_kitchen"),
    _tool("disable_quota_guard"),
    _tool("lock_ingredients", ("locked", "pipeline_id", "unlock")),
    _tool("reload_session"),
    _tool("record_pipeline_step", ("pipeline_id", "op", "dependencies", "step_name")),
    _tool("get_pr_reviews", ("pr_number", "cwd", "repo"), required=("pr_number", "cwd")),
    _tool(
        "bulk_close_issues",
        ("issue_numbers", "comment", "cwd"),
        required=("issue_numbers", "comment", "cwd"),
    ),
    _tool("list_recipes"),
    _tool(
        "load_recipe",
        ("name", "overrides", "ingredients_only", "delivery_request"),
        required=("name",),
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
            "part",
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
        ),
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
    _tool("read_db", ("db_path", "query", "params", "timeout"), required=("db_path", "query")),
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


def get_tool_def(tool_name: str) -> ToolDef | None:
    return TOOL_REGISTRY.get(tool_name)


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
