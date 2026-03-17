"""Semantic rule: undefined-bash-placeholder

Validates that every {placeholder} in a SKILL.md bash block is either:
  - Declared as an ingredient in the skill's ## Arguments / ## Ingredients section
  - Assigned as a shell variable in one of the skill's bash blocks
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_bash_placeholders,
    extract_declared_ingredients,
    shell_vars_assigned,
)
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

# Search directories for SKILL.md resolution (patchable in tests via patch.object).
# When None (default), uses SkillResolver to find bundled skills.
SKILL_SEARCH_DIRS: list[Path] | None = None

_PSEUDOCODE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("implement-worktree", "test_command"),
        ("implement-worktree-no-merge", "test_command"),
        ("resolve-failures", "test_command"),
    }
)


def _resolve_skill_md(skill_name: str) -> Path | None:
    """Resolve a skill name to its SKILL.md path.

    When SKILL_SEARCH_DIRS is set (e.g., in tests), searches those directories.
    Otherwise uses SkillResolver to find the bundled skill.
    """
    if SKILL_SEARCH_DIRS is not None:
        for search_dir in SKILL_SEARCH_DIRS:
            skill_md = search_dir / skill_name / "SKILL.md"
            if skill_md.is_file():
                return skill_md
        return None
    from autoskillit.workspace import SkillResolver  # noqa: PLC0415

    skill_info = SkillResolver().resolve(skill_name)
    if skill_info is None:
        return None
    return skill_info.path  # skill_info.path IS the SKILL.md file


@semantic_rule(
    name="undefined-bash-placeholder",
    description=(
        "A SKILL.md bash block uses a {placeholder} that is not declared as an ingredient "
        "or assigned as a shell variable. The model will guess the value from ambient context."
    ),
)
def _check_undefined_bash_placeholder(ctx: ValidationContext) -> list[RuleFinding]:
    """Fire for any run_skill step whose SKILL.md has undefined bash-block placeholders."""
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

        skill_md = _resolve_skill_md(skill_name)
        if skill_md is None:
            continue  # unknown-skill-command rule handles missing skills

        content = skill_md.read_text(encoding="utf-8")
        bash_blocks = extract_bash_blocks(content)
        if not bash_blocks:
            continue

        used = extract_bash_placeholders(bash_blocks)
        if not used:
            continue

        declared = extract_declared_ingredients(content)
        assigned = shell_vars_assigned(bash_blocks)
        defined = declared | assigned
        allowlisted = {name for (sname, name) in _PSEUDOCODE_ALLOWLIST if sname == skill_name}
        undefined = used - defined - allowlisted

        if undefined:
            findings.append(
                RuleFinding(
                    rule="undefined-bash-placeholder",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' bash block uses undefined {{placeholder}}: "
                        f"{sorted(undefined)}. Declare as ingredient in ## Arguments, or capture "
                        f"at runtime as VARNAME=$(command)."
                    ),
                )
            )
    return findings
