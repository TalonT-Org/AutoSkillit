"""Semantic rules for CI polling patterns in recipe steps."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule


@semantic_rule(
    name="ci-polling-inline-shell",
    description="Flags run_cmd steps containing gh run commands — use wait_for_ci tool instead",
    severity=Severity.WARNING,
)
def _check_ci_polling_inline_shell(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue
        if "gh run watch" in cmd or "gh run list" in cmd:
            findings.append(
                RuleFinding(
                    rule="ci-polling-inline-shell",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses inline 'gh run' commands in run_cmd. "
                        "Use the wait_for_ci MCP tool instead for race-immune CI watching "
                        "with structured output."
                    ),
                )
            )
    return findings
