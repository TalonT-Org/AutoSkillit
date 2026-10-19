"""Opaque MCP inputs must match the reviewed allowlist."""

from __future__ import annotations

from typing import Any

import pytest

from autoskillit.core import ALL_VISIBILITY_TAGS
from autoskillit.core.tool_registry import TOOL_REGISTRY

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ALLOWED_OPAQUE_INPUTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("write_audit_semantic_result", "assessments[]"),  # _assessment
        ("write_standalone_audit_evidence", "assessments[]"),  # _assessment
        ("write_audit_disposition_bundle", "dispositions[]"),  # _disposition_rows
        ("finalize_review_audit", "dispositions[]"),  # _audit_finalize._disposition_records
        ("finalize_review_audit", "prior_resolved_findings[]"),  # _aggregation lenient reader
        ("delegate_evidence_reader", "role_data"),  # tools_evidence_reader._role_request
        ("post_pr_review", "comments[]"),  # GitHubReviewComment.from_wire
        ("dispatch_food_truck", "resume_checkpoint"),  # SessionCheckpoint.from_dict
        ("run_python", "args"),  # Free-form keyword arguments for an arbitrary callable
    }
)


async def _registered_tools():
    from autoskillit.server import mcp

    mcp._transforms.clear()
    try:
        mcp.enable(tags=set(ALL_VISIBILITY_TAGS))
        return await mcp.list_tools()
    finally:
        mcp._transforms.clear()


def _is_opaque(schema: dict[str, Any]) -> bool:
    if schema.get("type") == "object":
        return not schema.get("properties") and schema.get("additionalProperties", True) is True
    return not any(key in schema for key in ("type", "anyOf", "enum", "const"))


def _opaque_pointers(name: str, schema: dict[str, Any], path: str = "") -> set[tuple[str, str]]:
    pointers = {(name, path)} if _is_opaque(schema) else set()
    for field, child in schema.get("properties", {}).items():
        child_path = f"{path}.{field}" if path else field
        pointers.update(_opaque_pointers(name, child, child_path))
    if isinstance(items := schema.get("items"), dict):
        pointers.update(_opaque_pointers(name, items, f"{path}[]"))
    for branch in schema.get("anyOf", []):
        pointers.update(_opaque_pointers(name, branch, path))
    if isinstance(values := schema.get("additionalProperties"), dict):
        pointers.update(_opaque_pointers(name, values, f"{path}{{}}"))
    return pointers


@pytest.mark.anyio
async def test_opaque_inputs_are_explicitly_allowed():
    tools = await _registered_tools()
    assert {tool.name for tool in tools} == set(TOOL_REGISTRY)
    live = set().union(*(_opaque_pointers(tool.name, tool.parameters) for tool in tools))

    assert not (unexpected := live - _ALLOWED_OPAQUE_INPUTS), (
        f"Opaque tool inputs {sorted(unexpected)}: type new structured inputs with a TypedDict; "
        "add a new exception only for a reviewed free-form contract."
    )
    assert not (stale := _ALLOWED_OPAQUE_INPUTS - live), (
        f"Stale opaque-input exceptions: {sorted(stale)}"
    )
