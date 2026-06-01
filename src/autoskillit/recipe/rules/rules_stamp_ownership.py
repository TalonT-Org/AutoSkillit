"""Semantic rule: exclusive-stamp-ownership

Validates that no skill other than the designated owner writes a registered stamp string.
"""

from __future__ import annotations

from autoskillit.core import DRY_WALKTHROUGH_VERIFIED_MARKER, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import _resolve_skill_md
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_STAMP_OWNERS: dict[str, str] = {
    DRY_WALKTHROUGH_VERIFIED_MARKER: "dry-walkthrough",
}


def _has_write_instruction(content: str, stamp: str) -> bool:
    """Return True if the stamp appears in a write-instruction context."""
    backtick_ref = f"`{stamp}`"
    lines = content.splitlines()
    for line in lines:
        if stamp not in line:
            continue
        if backtick_ref in line:
            continue
        return True
    return False


@semantic_rule(
    name="exclusive-stamp-ownership",
    description=(
        "A SKILL.md contains instructions to write a registered stamp string that belongs "
        "exclusively to another skill. Only the designated owner may write each stamp."
    ),
    severity=Severity.ERROR,
)
def _check_exclusive_stamp_ownership(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill_cmd = step.with_args.get("skill_command", "")
        if not skill_cmd:
            continue
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        skill_md = _resolve_skill_md(skill_name, resolver=ctx.skill_resolver)
        if skill_md is None:
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        for stamp, owner in _STAMP_OWNERS.items():
            if skill_name == owner:
                continue
            if stamp not in content:
                continue
            if _has_write_instruction(content, stamp):
                findings.append(
                    RuleFinding(
                        rule="exclusive-stamp-ownership",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Skill '{skill_name}' contains the stamp "
                            f"'{stamp}' which is exclusively owned by '{owner}'. "
                            f"Only '{owner}' may write this stamp."
                        ),
                    )
                )
    return findings
