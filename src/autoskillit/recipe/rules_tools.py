"""Semantic rules for MCP tool name validity."""
from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_ALL_TOOLS: frozenset[str] = GATED_TOOLS | UNGATED_TOOLS


@semantic_rule(
    name="unknown-tool",
    description="step.tool must be a registered MCP tool name",
    severity=Severity.ERROR,
)
def _unknown_tool(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is not None and step.tool not in _ALL_TOOLS:
            findings.append(
                RuleFinding(
                    rule="unknown-tool",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': tool '{step.tool}' is not a registered MCP tool. "
                        f"Known tools: {sorted(_ALL_TOOLS)}"
                    ),
                )
            )
    return findings
