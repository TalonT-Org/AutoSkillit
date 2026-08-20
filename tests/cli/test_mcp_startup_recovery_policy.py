"""Rendered MCP startup-recovery prompt contracts."""

from __future__ import annotations

import pytest

from autoskillit.cli.prompts import _prompts

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_startup_policy_is_attempt_bounded_and_has_one_terminal_message() -> None:
    spec = _prompts._MCP_STARTUP_RECOVERY_SPEC

    assert spec.attempt_cap > 1
    assert spec.render().count(spec.exhaustion_message) == 1


def test_rendered_contract_has_explicit_phase_boundary_and_all_silence_atoms() -> None:
    rendered = _prompts._MCP_STARTUP_RECOVERY_SPEC.render()
    pre_dispatch, post_receipt = rendered.split(
        "MCP STARTUP RECOVERY — POST-RECEIPT:",
        maxsplit=1,
    )

    assert "MCP STARTUP RECOVERY — PRE-DISPATCH:" in pre_dispatch
    assert "every failure" in pre_dispatch.lower()
    assert "before classifying" in pre_dispatch.lower()
    assert "do not explain" in pre_dispatch.lower()
    assert "do not troubleshoot" in pre_dispatch.lower()
    assert "do not output a free-text question" in pre_dispatch.lower()
    assert "do not call AskUserQuestion" in pre_dispatch
    assert "Receiving any CallToolResult ends PRE-DISPATCH recovery" in post_receipt


def test_every_clause_is_rendered_once_in_its_declared_phase() -> None:
    spec = _prompts._MCP_STARTUP_RECOVERY_SPEC
    rendered = spec.render()
    pre_dispatch, post_receipt = rendered.split(
        "MCP STARTUP RECOVERY — POST-RECEIPT:",
        maxsplit=1,
    )

    for clause in spec.clauses:
        assert rendered.count(clause.render()) == 1
        owner = pre_dispatch if clause.scope == "pre_dispatch" else post_receipt
        assert clause.render() in owner


def test_canonical_instruction_is_rendered_from_the_policy() -> None:
    assert _prompts._MCP_RETRY_INSTRUCTION == _prompts._MCP_STARTUP_RECOVERY_SPEC.render()


def test_startup_policy_preserves_attested_skill_input_shape() -> None:
    rendered = _prompts._MCP_STARTUP_RECOVERY_SPEC.render()

    assert (
        "For structured child inputs, select "
        "recipe_execution.skill_input_shapes[step_name], initialize skill_inputs "
        "with exactly its ordered keys, and replace available values in place. "
        "For unavailable context, copy only that key's advertised "
        'absence_values entry by key presence, so "", 0, and False remain '
        "verbatim; never delete or invent a key."
    ) in rendered
