"""Semantic rules: detect inline shell scripts in run_cmd cmd fields."""

from __future__ import annotations

import re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_INLINE_SCRIPT_ALLOWLIST: frozenset[str] = frozenset()

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

_CONTROL_FLOW_RE = re.compile(
    r"""
    (?<![.a-zA-Z_/])      # not preceded by word char or path separator
    (?:
        \bif\s+.*;\s*then\b |
        \bthen\b |
        \belse\b |
        \bfi\b |
        \bfor\s+\w+\s+in\b |
        \bwhile\s+.*;\s*do\b |
        \bdo\b |
        \bdone\b |
        \bcase\s+.*\s+in\b |
        \besac\b
    )
    """,
    re.VERBOSE,
)

_JQ_BLOCK_RE = re.compile(
    r"--jq\s+'[^']*'|--jq\s+\"[^\"]*\"|jq\s+'[^']*'|jq\s+\"[^\"]*\"",
    re.DOTALL,
)

_LOOP_RE = re.compile(r"\bfor\s+\w+\s+in\s+.*;\s*do\b|\bwhile\s+.*;\s*do\b")

_BASH_BUILTINS_RE = re.compile(r"\b(?:mapfile|read\s+-r|declare|local|export)\b")

_VAR_ASSIGN_RE = re.compile(r"^[A-Z_][A-Z0-9_]*=", re.MULTILINE)

_AND_CHAIN_RE = re.compile(r"&&")

_PYTHON3_C_RE = re.compile(r"\bpython3?\s+-c\b")


def _strip_jq_blocks(cmd: str) -> str:
    """Remove jq expression blocks to avoid false-positive control-flow matches."""
    return _JQ_BLOCK_RE.sub("", cmd)


def _count_logical_lines(cmd: str) -> int:
    """Count non-blank, non-comment lines."""
    return sum(1 for line in cmd.splitlines() if line.strip() and not line.strip().startswith("#"))


@semantic_rule(
    name="inline-script-in-cmd",
    description=(
        "Detects inline shell scripts in run_cmd cmd fields "
        "(control flow, loops, excessive variable assignments)"
    ),
    severity=Severity.ERROR,
)
def _check_inline_script_in_cmd(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        if name in _INLINE_SCRIPT_ALLOWLIST:
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue

        stripped = _strip_jq_blocks(cmd)

        if _CONTROL_FLOW_RE.search(stripped):
            findings.append(
                RuleFinding(
                    rule="inline-script-in-cmd",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' contains shell control flow in cmd. "
                        "Extract to a .sh script or run_python callable."
                    ),
                )
            )
            continue

        if _LOOP_RE.search(stripped):
            findings.append(
                RuleFinding(
                    rule="inline-script-in-cmd",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' contains a shell loop in cmd. "
                        "Extract to a .sh script or run_python callable."
                    ),
                )
            )
            continue

        if _BASH_BUILTINS_RE.search(stripped):
            findings.append(
                RuleFinding(
                    rule="inline-script-in-cmd",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' contains bash builtins "
                        "(mapfile/declare/local/export/read -r) in cmd. "
                        "Extract to a .sh script."
                    ),
                )
            )
            continue

        var_count = len(_VAR_ASSIGN_RE.findall(cmd))
        chain_count = len(_AND_CHAIN_RE.findall(cmd))
        if chain_count > 3 and var_count >= 1:
            findings.append(
                RuleFinding(
                    rule="inline-script-in-cmd",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' has {chain_count} &&-chains with "
                        f"{var_count} variable assignment(s). "
                        "Extract to a .sh script or run_python callable."
                    ),
                )
            )
            continue

        logical_lines = _count_logical_lines(cmd)
        if logical_lines > 3 and var_count > 2:
            findings.append(
                RuleFinding(
                    rule="inline-script-in-cmd",
                    severity=Severity.WARNING,
                    step_name=name,
                    message=(
                        f"Step '{name}' has {logical_lines} logical lines and "
                        f"{var_count} variable assignments. "
                        "Consider extracting to a script."
                    ),
                )
            )

    return findings


@semantic_rule(
    name="inline-python-in-cmd",
    description=(
        "Detects python3 -c usage in run_cmd cmd fields (must use run_python callable instead)"
    ),
    severity=Severity.ERROR,
)
def _check_inline_python_in_cmd(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for name, step in ctx.recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        if name in _INLINE_SCRIPT_ALLOWLIST:
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        if not isinstance(cmd, str):
            continue

        if _PYTHON3_C_RE.search(cmd):
            findings.append(
                RuleFinding(
                    rule="inline-python-in-cmd",
                    severity=Severity.ERROR,
                    step_name=name,
                    message=(
                        f"Step '{name}' uses python3 -c in cmd. Convert to a run_python callable."
                    ),
                )
            )

    return findings
