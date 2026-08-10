"""Canonical IL-0 tool registry parity with live MCP handlers."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.core.tool_registry as tool_registry
from autoskillit.core import (
    EXPLORATION_TOOLS,
    TOOL_REGISTRY,
    ToolInitializationOperation,
    ToolParamDef,
    ToolParamRole,
    ToolWireType,
    compute_tool_contract_identity,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _handler_signatures(
    tools_dir: Path | None = None,
) -> dict[str, tuple[tuple[str, bool], ...]]:
    tools_dir = tools_dir or (
        Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "server" / "tools"
    )
    handlers: dict[str, tuple[tuple[str, bool], ...]] = {}
    for path in sorted(tools_dir.glob("tools_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                for decorator in node.decorator_list
            ):
                continue
            positional = [*node.args.posonlyargs, *node.args.args]
            defaults: list[ast.expr | None] = [None] * (len(positional) - len(node.args.defaults))
            defaults.extend(node.args.defaults)
            pairs = [
                *zip(positional, defaults, strict=True),
                *zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True),
            ]
            assert node.name not in handlers, f"duplicate MCP tool registration: {node.name}"
            handlers[node.name] = tuple(
                (argument.arg, default is None)
                for argument, default in pairs
                if argument.arg != "ctx"
            )
    return handlers


def test_handler_collection_rejects_duplicate_registrations(tmp_path: Path) -> None:
    (tmp_path / "tools_duplicate.py").write_text(
        "@mcp.tool()\ndef duplicate(): ...\n\n@mcp.tool()\nasync def duplicate(): ...\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="duplicate MCP tool registration: duplicate"):
        _handler_signatures(tmp_path)


def test_registry_matches_handler_names_bidirectionally() -> None:
    assert set(TOOL_REGISTRY) == set(_handler_signatures())


def test_tool_builder_rejects_roles_for_unknown_parameters() -> None:
    with pytest.raises(ValueError, match="unknown parameter.*typo"):
        tool_registry._tool(
            "run_cmd",
            ("cmd",),
            roles={"typo": ToolParamRole.PROTOCOL},
        )


def test_registry_matches_handler_order_and_requiredness() -> None:
    for name, handler_params in _handler_signatures().items():
        registry_params = tuple(
            (param.name, param.required)
            for param in TOOL_REGISTRY[name].params
            if param.handler_parameter
        )
        assert registry_params == handler_params, name


def test_registry_preserves_typed_handler_wire_contracts() -> None:
    expected = {
        "configure_fleet": {
            "max_concurrent_dispatches": ToolWireType.INTEGER,
            "default_timeout_sec": ToolWireType.INTEGER,
            "max_extension_seconds": ToolWireType.INTEGER,
            "idle_output_timeout": ToolWireType.INTEGER,
            "acquire_timeout_sec": ToolWireType.INTEGER,
            "enable_deadline_extension": ToolWireType.BOOLEAN,
        },
        "configure_order": {
            "timeout": ToolWireType.INTEGER,
            "stale_threshold": ToolWireType.INTEGER,
            "idle_output_timeout": ToolWireType.INTEGER,
            "max_suppression_seconds": ToolWireType.INTEGER,
        },
        "record_gate_dispatch": {"approved": ToolWireType.BOOLEAN},
        "reset_dispatch": {
            "force": ToolWireType.BOOLEAN,
            "destroy_artifacts": ToolWireType.BOOLEAN,
        },
        "open_kitchen": {"ingredients_only": ToolWireType.BOOLEAN},
        "load_recipe": {"ingredients_only": ToolWireType.BOOLEAN},
        "post_pr_review": {
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
    }

    for tool_name, wire_types in expected.items():
        for param_name, wire_type in wire_types.items():
            param = TOOL_REGISTRY[tool_name].param_def(param_name)
            assert param is not None
            assert param.wire_type is wire_type


def test_managed_launch_tools_share_native_shell_capture_schema() -> None:
    run_skill_param = TOOL_REGISTRY["run_skill"].param_def("native_shell_capture_mode")
    food_truck_param = TOOL_REGISTRY["dispatch_food_truck"].param_def("native_shell_capture_mode")

    assert run_skill_param == food_truck_param
    assert run_skill_param is not None
    assert run_skill_param.wire_type is ToolWireType.STRING
    assert run_skill_param.required is False
    assert run_skill_param.handler_parameter is True

    signatures = _handler_signatures()
    for tool_name in ("run_skill", "dispatch_food_truck"):
        assert ("native_shell_capture_mode", False) in signatures[tool_name]


def test_run_skill_has_one_compiler_owned_structured_input_channel() -> None:
    structured = tuple(
        param for param in TOOL_REGISTRY["run_skill"].params if param.structured_skill_inputs
    )
    assert tuple(param.name for param in structured) == ("skill_inputs",)
    assert structured[0].handler_parameter


def test_tool_def_freezes_caller_owned_parameter_sequences() -> None:
    params = [ToolParamDef("first", role=ToolParamRole.CHILD_INPUT)]

    tool = replace(TOOL_REGISTRY["close_kitchen"], params=params)
    params.append(ToolParamDef("later", role=ToolParamRole.CHILD_INPUT))

    assert tool.params == (ToolParamDef("first", role=ToolParamRole.CHILD_INPUT),)


def test_tool_def_rejects_non_parameter_definitions() -> None:
    with pytest.raises(TypeError, match="parameter 0 must be a ToolParamDef"):
        replace(TOOL_REGISTRY["close_kitchen"], params=(object(),))


def test_tool_contract_identity_tracks_registry_parameter_shape() -> None:
    run_skill = TOOL_REGISTRY["run_skill"]
    changed = replace(
        run_skill,
        params=(
            *run_skill.params,
            ToolParamDef("future_parameter", role=ToolParamRole.CHILD_INPUT),
        ),
    )

    assert compute_tool_contract_identity(changed) != compute_tool_contract_identity(run_skill)


def test_every_tool_has_an_explicit_initialization_operation() -> None:
    expected = {
        ToolInitializationOperation.RECOVERY: {
            "complete_recipe_initialization",
            "complete_run_skill_result",
            "get_recipe_section",
            "recover_run_skill_result",
        },
        ToolInitializationOperation.INSPECTION: {
            "analyze_tool_sequences",
            "check_pr_mergeable",
            "check_repo_merge_state",
            "fetch_github_issue",
            "get_ci_status",
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
            "validate_recipe",
        }
        | EXPLORATION_TOOLS,
        ToolInitializationOperation.LIFECYCLE_CONTROL: {
            "close_kitchen",
            "open_kitchen",
        },
        ToolInitializationOperation.EXECUTION: {
            "run_cmd",
            "run_python",
            "run_skill",
            "test_check",
        },
        ToolInitializationOperation.MUTATION: {
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
            "enable_exploration",
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
        },
    }
    actual = {
        operation: {
            name
            for name, definition in TOOL_REGISTRY.items()
            if definition.initialization_operation is operation
        }
        for operation in ToolInitializationOperation
    }

    assert actual == expected


def test_tool_contract_identity_tracks_initialization_operation() -> None:
    run_skill = TOOL_REGISTRY["run_skill"]
    changed = replace(
        run_skill,
        initialization_operation=ToolInitializationOperation.RECOVERY,
    )

    assert compute_tool_contract_identity(changed) != compute_tool_contract_identity(run_skill)
