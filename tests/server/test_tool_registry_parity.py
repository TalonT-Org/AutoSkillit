"""Canonical IL-0 tool registry parity with live MCP handlers."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    TOOL_REGISTRY,
    ToolParamDef,
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
            "max_total_issues": ToolWireType.INTEGER,
            "default_timeout_sec": ToolWireType.INTEGER,
            "max_extension_seconds": ToolWireType.INTEGER,
            "idle_output_timeout": ToolWireType.INTEGER,
            "acquire_timeout_sec": ToolWireType.INTEGER,
            "max_issues_per_food_truck": ToolWireType.INTEGER,
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
    }

    for tool_name, wire_types in expected.items():
        for param_name, wire_type in wire_types.items():
            param = TOOL_REGISTRY[tool_name].param_def(param_name)
            assert param is not None
            assert param.wire_type is wire_type


def test_run_skill_has_one_compiler_owned_structured_input_channel() -> None:
    structured = tuple(
        param for param in TOOL_REGISTRY["run_skill"].params if param.structured_skill_inputs
    )
    assert tuple(param.name for param in structured) == ("skill_inputs",)
    assert structured[0].handler_parameter


def test_tool_def_freezes_caller_owned_parameter_sequences() -> None:
    params = [ToolParamDef("first")]

    tool = replace(TOOL_REGISTRY["close_kitchen"], params=params)
    params.append(ToolParamDef("later"))

    assert tool.params == (ToolParamDef("first"),)


def test_tool_def_rejects_non_parameter_definitions() -> None:
    with pytest.raises(TypeError, match="parameter 0 must be a ToolParamDef"):
        replace(TOOL_REGISTRY["close_kitchen"], params=(object(),))


def test_tool_contract_identity_tracks_registry_parameter_shape() -> None:
    run_skill = TOOL_REGISTRY["run_skill"]
    changed = replace(
        run_skill,
        params=(*run_skill.params, ToolParamDef("future_parameter")),
    )

    assert compute_tool_contract_identity(changed) != compute_tool_contract_identity(run_skill)
