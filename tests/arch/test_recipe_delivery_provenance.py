"""AST guards for independent recipe-delivery provenance domains."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_DELIVERY_BOUNDS = (
    Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "core" / "_delivery_bounds.py"
)


def _resolver() -> ast.FunctionDef:
    tree = ast.parse(_DELIVERY_BOUNDS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_recipe_delivery_decision":
            return node
    pytest.fail("resolve_recipe_delivery_decision not found")


def _assigned_expression(function: ast.FunctionDef, target_name: str) -> ast.expr:
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == target_name
        ):
            return node.value
    pytest.fail(f"assignment to {target_name!r} not found")


def test_requested_and_observed_limits_have_independent_sources() -> None:
    function = _resolver()
    requested = ast.dump(_assigned_expression(function, "requested"))
    observed = ast.dump(_assigned_expression(function, "observed"))

    assert "caller_requested_outer_tokens" in requested
    assert "request" in requested
    assert "host_observed_requested_outer_tokens" not in requested

    assert "host_observed_requested_outer_tokens" in observed
    assert "attestation" in observed
    assert "caller_requested_outer_tokens" not in observed


def test_selected_limit_never_comes_from_history_or_measured_size() -> None:
    function = _resolver()
    body = ast.dump(function)
    assert "history_retention_token_limit" not in body
    assert "measured_recipe_exemption_max_utf8_bytes" not in body

    selected_values = [
        keyword.value
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        for keyword in call.keywords
        if keyword.arg == "selected_limit"
    ]
    assert selected_values, "resolver must pass an explicit selected limit on every decision path"
    for value in selected_values:
        value_dump = ast.dump(value)
        assert "required_serialized_tokens" not in value_dump
        assert "history_retention_token_limit" not in value_dump


def test_resolver_does_not_treat_wire_metadata_or_rollouts_as_authority() -> None:
    body = ast.dump(_resolver()).lower()
    for untrusted_source in ("_meta", "rollout", "trace", "tool_output_token_limit"):
        assert untrusted_source not in body
