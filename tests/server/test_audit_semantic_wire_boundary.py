"""Registered-schema and argument-validation boundary for ArtifactRef-bearing audit tool inputs."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

import autoskillit.server as server
from autoskillit.core import ArtifactRef, compute_bytes_hash
from autoskillit.server.tools import tools_audit_artifacts

pytestmark = [pytest.mark.layer("server"), pytest.mark.small, pytest.mark.anyio]


def _arguments(tmp_path: Path, tool_name: str) -> dict[str, Any]:
    ref = ArtifactRef(
        locator=str(tmp_path / "plan.md"),
        media_type="text/markdown",
        schema_version=1,
        byte_size=4,
        content_digest=compute_bytes_hash(b"plan"),
    )
    arguments: dict[str, Any] = {
        "audited_plan_refs": [ref.to_dict()],
        "assessments": [
            {
                "requirement_id": "REQ-5291",
                "requirement_text": "Advertise and enforce the reference wire shape.",
                "assessment": "COVERED",
                "evidence_summary": "The registered tool validates the submitted fields.",
            }
        ],
        "verdict": "GO",
    }
    if tool_name == "write_audit_semantic_result":
        arguments["reservation_handle"] = "wire-boundary-test"
    return arguments


async def _call_at_boundary(name: str, arguments: dict[str, object]) -> dict[str, Any]:
    result = await server.mcp.call_tool(
        name, json.loads(json.dumps(arguments)), run_middleware=False
    )
    return json.loads(result.content[0].text)


@pytest.mark.parametrize(
    "tool_name", ("write_audit_semantic_result", "write_standalone_audit_evidence")
)
@pytest.mark.parametrize("position", ("audited_plan_refs", "remediation_ref"))
async def test_artifact_ref_positions_advertise_reference_contract(
    kitchen_enabled, tool_name, position
):
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    schema = tools[tool_name].parameters
    parameter = schema["properties"][position]
    if position == "audited_plan_refs":
        node = parameter["items"]
    else:
        assert {"type": "null"} in parameter["anyOf"]
        assert parameter["default"] is None
        assert position not in schema["required"]
        node = next(branch for branch in parameter["anyOf"] if branch.get("type") != "null")

    fields = {field.name for field in dataclasses.fields(ArtifactRef)}
    assert set(node["properties"]) == set(node["required"]) == fields
    assert node.get("additionalProperties") is not True
    for field in ("locator", "media_type", "content_digest"):
        assert node["properties"][field]["type"] == "string"
    for field in ("schema_version", "byte_size"):
        assert node["properties"][field]["type"] == "integer"


@pytest.mark.parametrize(
    ("tool_name", "position", "field", "value", "error_type"),
    [
        *(
            (tool_name, position, "schema_version", "1", "int_type")
            for tool_name in ("write_audit_semantic_result", "write_standalone_audit_evidence")
            for position in ("audited_plan_refs", "remediation_ref")
        ),
        ("write_audit_semantic_result", "audited_plan_refs", "schema_version", True, "int_type"),
        ("write_audit_semantic_result", "audited_plan_refs", "schema_version", 1.0, "int_type"),
        ("write_audit_semantic_result", "audited_plan_refs", "byte_size", "8100", "int_type"),
        ("write_audit_semantic_result", "audited_plan_refs", "byte_size", None, "missing"),
        ("write_audit_semantic_result", "audited_plan_refs", "schemaVersion", None, "missing"),
    ],
)
async def test_malformed_reference_rejected_at_boundary(
    kitchen_enabled, monkeypatch, tmp_path, tool_name, position, field, value, error_type
):
    arguments = _arguments(tmp_path, tool_name)
    ref = arguments["audited_plan_refs"][0]
    if position == "remediation_ref":
        ref = ref.copy()
        arguments["remediation_ref"] = ref
        arguments["verdict"] = "NO GO"
    if field == "schemaVersion":
        ref[field] = ref.pop("schema_version")
    elif value is None:
        del ref[field]
    else:
        ref[field] = value

    builder = Mock(side_effect=RuntimeError("handler reached"))
    context = Mock(side_effect=RuntimeError("handler reached"))
    monkeypatch.setattr(tools_audit_artifacts, "_build_semantic_result", builder)
    monkeypatch.setattr(server, "_get_ctx", context)
    with pytest.raises(ValidationError) as rejected:
        await _call_at_boundary(tool_name, arguments)

    errors = rejected.value.errors()
    expected_field = "schema_version" if field == "schemaVersion" else field
    prefix = ("audited_plan_refs", 0) if position == "audited_plan_refs" else ("remediation_ref",)
    assert len(errors) == 1
    assert errors[0]["loc"] == (*prefix, expected_field)
    assert errors[0]["type"] == error_type
    builder.assert_not_called()
    context.assert_not_called()


@pytest.mark.parametrize("shape", ("extra_keys", "explicit_null", "omitted"))
async def test_valid_reference_shapes_reach_handler(kitchen_enabled, monkeypatch, tmp_path, shape):
    arguments = _arguments(tmp_path, "write_audit_semantic_result")
    expected_ref = ArtifactRef.from_dict(arguments["audited_plan_refs"][0])
    if shape == "extra_keys":
        arguments["audited_plan_refs"][0]["note"] = "x"
        arguments["remediation_ref"] = {**expected_ref.to_dict(), "note": "x"}
        arguments["verdict"] = "NO GO"
    elif shape == "explicit_null":
        arguments["remediation_ref"] = None

    builder = Mock(side_effect=RuntimeError("recorded"))
    monkeypatch.setattr(tools_audit_artifacts, "_build_semantic_result", builder)
    result = await _call_at_boundary("write_audit_semantic_result", arguments)

    builder.assert_called_once()
    assert result["success"] is False
    assert "recorded" in result["error"]
    received = builder.call_args.kwargs
    assert ArtifactRef.from_dict(received["audited_plan_refs"][0]) == expected_ref
    if shape == "extra_keys":
        assert ArtifactRef.from_dict(received["remediation_ref"]) == expected_ref
    else:
        assert received["remediation_ref"] is None


async def test_domain_reference_rules_reported_in_envelope(kitchen_enabled, tmp_path):
    arguments = _arguments(tmp_path, "write_audit_semantic_result")
    arguments["audited_plan_refs"][0]["schema_version"] = 0

    result = await _call_at_boundary("write_audit_semantic_result", arguments)

    assert result["success"] is False
    assert "ArtifactRef.schema_version" in result["error"]
