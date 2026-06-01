"""Semantic rule: exclusive-stamp-ownership

Validates that no skill other than the designated owner writes a registered stamp string.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import DRY_WALKTHROUGH_VERIFIED_MARKER, Severity

if TYPE_CHECKING:
    from autoskillit.core import SkillResolver
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

SKILL_SEARCH_DIRS: list[Path] | None = None

_STAMP_OWNERS: dict[str, str] = {
    DRY_WALKTHROUGH_VERIFIED_MARKER: "dry-walkthrough",
}


def _resolve_skill_md(skill_name: str, *, resolver: SkillResolver | None = None) -> Path | None:
    if SKILL_SEARCH_DIRS is not None:
        for search_dir in SKILL_SEARCH_DIRS:
            skill_md = search_dir / skill_name / "SKILL.md"
            if skill_md.is_file():
                return skill_md
        return None
    if resolver is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        resolver = DefaultSkillResolver()
    skill_info = resolver.resolve(skill_name)
    if skill_info is None:
        return None
    return skill_info.path


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
            if stamp in content:
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
