"""Canonical stdlib-only MCP tool metadata.

Handler parameters mirror the public ``@mcp.tool`` signatures.  ``skill_inputs``
is the sole compiler-owned structured recipe parameter; the next server phase
will expose that already-compiled channel on ``run_skill``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ._tool_registry_builders import _run_skill, _tool
from .closure_hashing import compute_canonical_hash
from .types._type_constants_registries import HEADLESS_TOOLS
from .types._type_recipe_binding import (
    RUNTIME_ADMISSION_BY_ROLE,
    RuntimeAdmission,
    ToolDef,
    ToolParamRole,
    ToolWireType,
)

__all__ = [
    "TOOL_REGISTRY",
    "EXECUTION_TUNING_EXTERNALLY_RESOLVED",
    "EXECUTION_TUNING_STEP_FIELDS",
    "all_tool_names",
    "build_parameter_forwarding_rules",
    "compute_tool_contract_identity",
    "get_tool_def",
    "runtime_exempt_param_names",
    "unsupported_tool_params",
]

_TOOL_CONTRACT_IDENTITY_DOMAIN = "autoskillit:tool-contract:v1:sha256"

# RecipeStep fallbacks for execution-tuning parameters left at their vacancy sentinel.
# `_run_skill_prepare.py` keeps explicit branches because the sentinels differ by type.
EXECUTION_TUNING_STEP_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "model": "model",
        "stale_threshold": "stale_threshold",
        "idle_output_timeout": "idle_output_timeout",
    }
)

# Execution-tuning parameters resolved outside the prepare-phase fallback block.
EXECUTION_TUNING_EXTERNALLY_RESOLVED: Mapping[str, str] = MappingProxyType(
    {
        # Pre-gate profile resolution — see the step_provider_resolved_from_recipe
        # block earlier in run_skill().
        "step_provider": "provider",
    }
)


_TOOL_DEFS = (
    _tool(
        "delegate_evidence_reader",
        ("role", "role_data"),
        required=("role", "role_data"),
        wire_types={
            "role": ToolWireType.STRING,
            "role_data": ToolWireType.OBJECT,
        },
    ),
    _tool(
        "read_authorized_artifact",
        ("page_size",),
        wire_types={"page_size": ToolWireType.INTEGER},
    ),
    _tool(
        "get_authorized_artifact_page",
        ("continuation", "page_size"),
        required=("continuation",),
        wire_types={
            "continuation": ToolWireType.STRING,
            "page_size": ToolWireType.INTEGER,
        },
    ),
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
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
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
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
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
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
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
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
    ),
    _tool("get_ci_status", ("branch", "run_id", "repo", "workflow", "event", "cwd")),
    _tool(
        "submit_exploration_query",
        ("query", "max_results", "_autoskillit_exploration_request_token"),
        required=("query",),
        wire_types={"max_results": ToolWireType.INTEGER},
        roles={"_autoskillit_exploration_request_token": ToolParamRole.ORCHESTRATOR_SCOPING},
    ),
    _tool(
        "get_exploration_page",
        ("page_size", "continuation", "_autoskillit_exploration_request_token"),
        wire_types={"page_size": ToolWireType.INTEGER},
        roles={"_autoskillit_exploration_request_token": ToolParamRole.ORCHESTRATOR_SCOPING},
    ),
    _tool(
        "resume_exploration_context",
        ("page_size", "_autoskillit_exploration_request_token"),
        wire_types={"page_size": ToolWireType.INTEGER},
        roles={"_autoskillit_exploration_request_token": ToolParamRole.ORCHESTRATOR_SCOPING},
    ),
    _tool(
        "inspect_session_logs",
        (
            "operation",
            "session_ids",
            "session_id",
            "artifact",
            "query",
            "continuation",
            "byte_limit",
        ),
        required=("operation",),
        wire_types={
            "session_ids": ToolWireType.ARRAY,
            "byte_limit": ToolWireType.INTEGER,
        },
    ),
    _tool(
        "clone_repo",
        ("source_dir", "run_name", "branch", "strategy", "remote_url", "step_name"),
        required=("source_dir", "run_name"),
    ),
    _tool(
        "remove_clone",
        ("clone_path", "keep", "step_name", "infrastructure_fault_override_reason"),
        required=("clone_path",),
    ),
    _tool(
        "push_to_remote",
        ("clone_path", "branch", "source_dir", "remote_url", "force", "step_name"),
        required=("clone_path", "branch"),
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
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
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
    ),
    _tool(
        "configure_fleet",
        (
            "max_concurrent_dispatches",
            "default_timeout_sec",
            "max_extension_seconds",
            "idle_output_timeout",
            "acquire_timeout_sec",
            "enable_deadline_extension",
            "inspector_model",
            "default_model",
            "model_override",
        ),
        wire_types={
            "max_concurrent_dispatches": ToolWireType.INTEGER,
            "default_timeout_sec": ToolWireType.INTEGER,
            "max_extension_seconds": ToolWireType.INTEGER,
            "idle_output_timeout": ToolWireType.INTEGER,
            "acquire_timeout_sec": ToolWireType.INTEGER,
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
            "model_override",
        ),
        wire_types={
            "timeout": ToolWireType.INTEGER,
            "stale_threshold": ToolWireType.INTEGER,
            "idle_output_timeout": ToolWireType.INTEGER,
            "max_suppression_seconds": ToolWireType.INTEGER,
        },
    ),
    _tool(
        "run_cmd",
        ("cmd", "cwd", "timeout", "step_name"),
        required=("cmd", "cwd"),
        recovery_recipe_delivery=True,
    ),
    _tool(
        "run_python",
        ("callable", "args", "timeout", "work_dir", "step_name"),
        required=("callable",),
        wire_types={"args": ToolWireType.OBJECT},
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
    ),
    _run_skill(),
    _tool("recover_run_skill_result", ()),
    _tool(
        "complete_run_skill_result",
        ("receipt_id",),
        required=("receipt_id",),
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
    ),
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
        # Must match run_skill's native_shell_capture_mode role exactly:
        # test_tool_registry_parity.py::test_managed_launch_tools_share_native_shell_capture_schema
        # asserts full dataclass equality between the two ToolParamDef instances.
        roles={"native_shell_capture_mode": ToolParamRole.SESSION_FLOW},
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
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
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
    _tool(
        "check_pr_mergeable",
        ("pr_number", "cwd", "repo", "step_name"),
        required=("pr_number", "cwd"),
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
    ),
    _tool(
        "create_and_publish_branch",
        ("issue_slug", "run_name", "issue_number", "work_dir", "remote_url", "step_name"),
        required=("issue_slug", "run_name", "issue_number", "work_dir", "remote_url"),
        recovery_recipe_delivery=True,
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
        ("issue_url", "label", "allow_reentry", "step_name"),
        required=("issue_url",),
        recovery_recipe_delivery=True,
    ),
    _tool(
        "prepare_issue",
        ("title", "body", "repo", "labels", "dry_run", "split"),
        required=("title", "body"),
    ),
    _tool("claim_issue", ("issue_url", "label", "allow_reentry"), required=("issue_url",)),
    _tool(
        "release_issue",
        (
            "issue_url",
            "label",
            "target_branch",
            "staged_label",
            "fail_label",
            "close_issue",
            "step_name",
            "infrastructure_fault_override_reason",
        ),
        required=("issue_url",),
        recovery_recipe_delivery=True,
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
    _tool(
        "enable_exploration",
        ("project_dir", "_autoskillit_exploration_request_token"),
        roles={"_autoskillit_exploration_request_token": ToolParamRole.ORCHESTRATOR_SCOPING},
    ),
    _tool("lock_ingredients", ("locked", "pipeline_id", "unlock")),
    _tool(
        "declare_join_batch",
        ("skill_name", "assignments", "session_id", "top_level_parent"),
        required=("skill_name", "assignments", "session_id"),
        wire_types={"assignments": ToolWireType.ARRAY},
    ),
    _tool(
        "run_fixed_batch",
        ("skill_name", "assignments", "idempotency_key"),
        required=("skill_name", "assignments", "idempotency_key"),
        wire_types={"assignments": ToolWireType.ARRAY},
    ),
    _tool(
        "read_fixed_batch_result",
        ("skill_name", "batch_id", "result_reference", "assignment_id", "offset", "page_size"),
        required=("skill_name", "batch_id", "result_reference"),
        wire_types={"offset": ToolWireType.INTEGER, "page_size": ToolWireType.INTEGER},
    ),
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
    _tool(
        "test_check",
        ("worktree_path", "step_name"),
        required=("worktree_path",),
        automatic_recipe_delivery=True,
        recovery_recipe_delivery=True,
    ),
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


def runtime_exempt_param_names(tool_def: ToolDef) -> frozenset[str]:
    """Names always admitted by the runtime attestation gate, regardless of with:.

    Derived from RUNTIME_ADMISSION_BY_ROLE — the per-role admission policy
    declared alongside ToolParamRole. Every role not mapped to ALWAYS there
    must be compiled into the template's with: block to be admitted.
    """
    return frozenset(
        param.name
        for param in tool_def.params
        if RUNTIME_ADMISSION_BY_ROLE[param.role] is RuntimeAdmission.ALWAYS
    )


def build_parameter_forwarding_rules(tool_name: str = "run_skill") -> str:
    """Orchestration prose for which params may be forwarded, derived from
    RUNTIME_ADMISSION_BY_ROLE and the UNION of both param -> RecipeStep field
    mappings (EXECUTION_TUNING_STEP_FIELDS | EXECUTION_TUNING_EXTERNALLY_RESOLVED).

    Generated, not hand-copied, so the two cannot diverge the way
    tools_recipe.py's docstring and sous-chef/SKILL.md once did (#4707).
    """
    tool_def = get_tool_def(tool_name)
    if tool_def is None:
        raise ValueError(f"{tool_name!r} is not a registered tool")

    # The two tables are guaranteed disjoint and jointly total over the
    # EXECUTION_TUNING role, so their union is exactly the role's parameter set.
    field_by_param: dict[str, str] = {
        **EXECUTION_TUNING_STEP_FIELDS,
        **EXECUTION_TUNING_EXTERNALLY_RESOLVED,
    }
    tuning_param_names = sorted(
        param.name for param in tool_def.params if param.role is ToolParamRole.EXECUTION_TUNING
    )
    if not tuning_param_names:
        return ""

    rules: list[str] = [f"EXECUTION-TUNING FIELDS ({tool_name}) — server-resolved, not restated:"]
    for param_name in tuning_param_names:
        if param_name not in field_by_param:
            raise ValueError(
                f"{tool_name!r} param {param_name!r} is EXECUTION_TUNING-roled but has no "
                "EXECUTION_TUNING_STEP_FIELDS/EXECUTION_TUNING_EXTERNALLY_RESOLVED entry"
            )
        field_name = field_by_param[param_name]
        rules.append(
            f"- A step's `{field_name}:` field is resolved server-side; never include "
            f"`{param_name}` in a `{tool_name}` call for that step. A per-step call-time "
            f"override is expressed only by declaring `{param_name}` under that step's "
            "`with:` block — a static with: value there admits only that exact value; "
            "only a dynamically-bound value varies per call."
        )
    return "\n".join(rules)


def compute_tool_contract_identity(tool_def: ToolDef) -> str:
    """Hash every canonical parameter property that defines a tool contract."""
    return compute_canonical_hash(
        {
            "name": tool_def.name,
            "initialization_operation": tool_def.initialization_operation.value,
            # Parameter roles are server-side gate policy, not client-visible wire shape.
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
