"""Semantic rule: SKILL.md declared write scope must align with recipe output_dir.

A BOUNDED write scope bounds a recipe output_dir; UNRESTRICTED and INHERIT scopes
do not narrow it, and a skill without a valid declaration cannot be verified. An
iteration-scoped output_dir also requires the skill's prose write instructions to
use the dynamic write prefix so the agent can target the narrowed directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import assert_never

import regex as re

from autoskillit.core import Severity
from autoskillit.hooks._write_scope import WriteScope, WriteScopeKind, bounded_scope_contains
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import skill_write_scope
from autoskillit.recipe._skill_placeholder_parser import (
    extract_write_path_declarations,
    has_dynamic_write_path,
)
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeStep

_CONTEXT_TEMPLATE_RE = re.compile(r"\$\{\{\s*context\.[^}]+\}\}")
_AUTOSKILLIT_TEMP_RE = re.compile(r"\{\{AUTOSKILLIT_TEMP\}\}")
_TEMP_DIR_SUFFIX = "autoskillit/temp"
_AUTOSKILLIT_TEMP_LITERAL_RE = re.compile(rf"\.{_TEMP_DIR_SUFFIX}(?=/|$)")


def _static_base_prefix(output_dir: str) -> str:
    """Strip context template segments to get the static path base.

    For 'iter_${{ context.review_loop_count }}' suffix patterns, removes the last
    path component that contains a template expression, returning only the stable prefix.
    Example: '{{AUTOSKILLIT_TEMP}}/review-pr/iter_${{ context.review_loop_count }}'
          -> '{{AUTOSKILLIT_TEMP}}/review-pr'
    """
    parts = output_dir.rstrip("/").split("/")
    stable: list[str] = []
    for part in parts:
        if _CONTEXT_TEMPLATE_RE.search(part):
            break
        stable.append(part)
    return "/".join(stable)


def _has_iteration_scoping(output_dir: str) -> bool:
    """Return True if output_dir contains a ${{ context.* }} template segment."""
    return bool(_CONTEXT_TEMPLATE_RE.search(output_dir))


def _normalise_path(path: str) -> str:
    """Normalise a path for comparison by replacing both template and resolved temp prefixes."""
    path = _AUTOSKILLIT_TEMP_LITERAL_RE.sub("{{AUTOSKILLIT_TEMP}}", path)
    path = _AUTOSKILLIT_TEMP_RE.sub("{{AUTOSKILLIT_TEMP}}", path)
    return path.rstrip("/")


def _first_misaligned_write_path(content: str, output_dir: str) -> str | None:
    if has_dynamic_write_path(content):
        return None
    declared_paths = extract_write_path_declarations(content)
    if not declared_paths:
        return None
    static_base = _static_base_prefix(output_dir)
    normalised_base = _normalise_path(static_base)
    for declared in declared_paths:
        normalised_declared = _normalise_path("{{AUTOSKILLIT_TEMP}}/" + declared)
        if Path(normalised_base).is_relative_to(Path(normalised_declared)):
            return declared
    return None


def _declared_boundary_error(
    write_scope: WriteScope | None, output_dir: str, project_dir: Path
) -> str | None:
    static_base = _static_base_prefix(output_dir)
    if not static_base or "${{" in static_base:
        return None
    if write_scope is None:
        return "cannot verify output_dir: skill lacks a valid write_paths declaration"
    match write_scope.kind:
        case WriteScopeKind.BOUNDED:
            if bounded_scope_contains(write_scope, static_base, str(project_dir)):
                return None
            return f"recipe output_dir {output_dir!r} is outside the skill's declared write scope"
        case WriteScopeKind.UNRESTRICTED | WriteScopeKind.INHERIT:
            return None
        case _ as unreachable:
            assert_never(unreachable)


def _eligible_skill_path(step: RecipeStep) -> tuple[str, str] | None:
    if step.tool != "run_skill":
        return None
    output_dir = (step.with_args or {}).get("output_dir", "") or ""
    if not output_dir:
        return None
    # Whole-worktree and work_dir destinations do not narrow the skill's write scope.
    if output_dir in (".", "${{ context.work_dir }}") or output_dir.strip("/") == "":
        return None
    skill_cmd = (step.with_args or {}).get("skill_command", "") or ""
    if not skill_cmd:
        return None
    skill_name = resolve_skill_name(skill_cmd)
    if skill_name is None:
        return None
    return output_dir, skill_name


@semantic_rule(
    name="skill-write-path-recipe-alignment",
    description=(
        "A SKILL.md's declared write scope does not match the recipe step's output_dir. "
        "The output_dir must remain inside a BOUNDED write scope (UNRESTRICTED and "
        "INHERIT scopes do not narrow it), and prose write instructions must respect "
        "an iteration-scoped output_dir."
    ),
    severity=Severity.ERROR,
)
def _check_skill_write_path_alignment(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        eligible = _eligible_skill_path(step)
        if eligible is None:
            continue
        output_dir, skill_name = eligible
        resolved = skill_write_scope(ctx, skill_name)
        if resolved is None:
            continue
        skill_md_path, content, write_scope = resolved

        boundary_error = _declared_boundary_error(
            write_scope, output_dir, ctx.project_dir or skill_md_path.parent.parent
        )
        if boundary_error is not None:
            findings.append(
                make_finding(
                    rule_name="skill-write-path-recipe-alignment",
                    step_name=step_name,
                    message=f"Skill '{skill_name}': {boundary_error}.",
                )
            )
            continue
        if not _has_iteration_scoping(output_dir):
            continue

        declared = _first_misaligned_write_path(content, output_dir)
        if declared is not None:
            findings.append(
                make_finding(
                    rule_name="skill-write-path-recipe-alignment",
                    step_name=step_name,
                    message=(
                        f"Skill '{skill_name}' NEVER block declares write scope "
                        f"'{declared}' but recipe output_dir '{output_dir}' enforces "
                        f"a narrower iteration-scoped prefix. The write guard will block "
                        f"all writes from the agent. Either update the SKILL.md to use "
                        f"${{AUTOSKILLIT_ALLOWED_WRITE_PREFIX}} for write paths, or align "
                        f"the output_dir to match the SKILL.md's declared scope."
                    ),
                )
            )

    return findings
