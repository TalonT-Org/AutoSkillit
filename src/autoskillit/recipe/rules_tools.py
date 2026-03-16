"""Semantic rules for MCP tool name validity."""

from __future__ import annotations

from autoskillit.core import GATED_TOOLS, HEADLESS_TOOLS, TOOL_SUBSET_TAGS, UNGATED_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_ALL_TOOLS: frozenset[str] = GATED_TOOLS | UNGATED_TOOLS | HEADLESS_TOOLS


@semantic_rule(
    name="constant-step-with-args",
    description="constant step must not have with args — there is no tool to receive them",
    severity=Severity.ERROR,
)
def _check_constant_step_no_with_args(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.constant is not None and step.with_args:
            findings.append(
                RuleFinding(
                    rule="constant-step-with-args",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}' is a constant step but has 'with' args "
                        f"({list(step.with_args.keys())}). "
                        f"constant steps have no tool to receive arguments."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="unknown-tool",
    description="step.tool must be a registered MCP tool name",
    severity=Severity.ERROR,
)
def _unknown_tool(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None:
            continue
        if step.tool not in _ALL_TOOLS:
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


@semantic_rule(
    name="subset-disabled-tool",
    description=(
        "step.tool belongs to a functional category currently disabled in subsets.disabled config"
    ),
    severity=Severity.WARNING,
)
def _check_subset_disabled_tool(ctx: ValidationContext) -> list[RuleFinding]:
    if not ctx.disabled_subsets:
        return []
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None or step.tool not in _ALL_TOOLS:
            continue
        tool_categories = TOOL_SUBSET_TAGS.get(step.tool, frozenset())
        overlap = tool_categories & ctx.disabled_subsets
        if overlap:
            disabled_subset = next(iter(sorted(overlap)))
            findings.append(
                RuleFinding(
                    rule="subset-disabled-tool",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"step '{step_name}': tool '{step.tool}' belongs to "
                        f"the disabled subset '{disabled_subset}'. Enable "
                        f"'{disabled_subset}' in .autoskillit/config.yaml "
                        f"subsets.disabled to use this tool."
                    ),
                )
            )
    return findings
