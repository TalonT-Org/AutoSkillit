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


def test_attestation_gate_and_annotation_ceiling_are_independent() -> None:
    """Attested gate tokens and annotation ceiling must never be compared.

    The annotated regime (char-gated via ``exemption_ceiling_chars``) and the
    unannotated regime (token-gated via ``attested_client_gate_tokens``) are
    independent admission channels — the resolver must never cross-compare a
    char ceiling against a token count.
    """
    function = _resolver()
    # Walk the AST for Compare nodes — no comparison should involve both
    # "attested_client_gate_tokens" and "exemption_ceiling_chars".
    for node in ast.walk(function):
        if isinstance(node, ast.Compare):
            names = {
                n.attr if isinstance(n, ast.Attribute) else n.id
                for n in ast.walk(node)
                if isinstance(n, (ast.Name, ast.Attribute))
            }
            assert not (
                "attested_client_gate_tokens" in names and "exemption_ceiling_chars" in names
            ), (
                "resolver cross-compares attested gate tokens with annotation ceiling — "
                "these are independent admission channels (token vs char)"
            )


def test_resolver_validates_attestation_gate_before_trusting() -> None:
    """The resolver must validate attested_client_gate_tokens before consuming it.

    A bare non-None check is insufficient — the gate must be compared against
    the expected injected value (CLAUDE_INJECTED_CLIENT_RESULT_TOKENS) so
    arbitrary positive attestations cannot bypass the token gate.
    """
    body = ast.dump(_resolver())
    # The resolver must reference CLAUDE_INJECTED_CLIENT_RESULT_TOKENS to
    # validate the attested gate — its name (or its re-export) must appear.
    assert "CLAUDE_INJECTED_CLIENT_RESULT_TOKENS" in body, (
        "resolver does not validate attested_client_gate_tokens against "
        "CLAUDE_INJECTED_CLIENT_RESULT_TOKENS — arbitrary attestation accepted"
    )
