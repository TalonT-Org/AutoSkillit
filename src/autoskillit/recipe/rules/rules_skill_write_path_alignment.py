"""Semantic rule: SKILL.md declared write scope must align with recipe output_dir.

When a recipe step narrows an agent's write scope via output_dir (e.g., adding
iter_N/ subdirectory scoping), but the SKILL.md still instructs the agent to
write to the broader path, the write guard blocks every write attempt. This rule
fires when the static NEVER block path in a SKILL.md is broader than the recipe's
output_dir static base prefix AND the SKILL.md does not use a dynamic write path
variable (AUTOSKILLIT_ALLOWED_WRITE_PREFIX or REVIEW_OUTPUT_DIR).
"""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import _resolve_skill_md
from autoskillit.recipe._skill_placeholder_parser import (
    extract_write_path_declarations,
    has_dynamic_write_path,
)
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

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


@semantic_rule(
    name="skill-write-path-recipe-alignment",
    description=(
        "A SKILL.md's declared write scope does not match the recipe step's output_dir. "
        "The write guard enforces output_dir, but the SKILL.md instructs the agent to "
        "write to a different path — causing all writes to be blocked."
    ),
    severity=Severity.ERROR,
)
def _check_skill_write_path_alignment(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        output_dir = (step.with_args or {}).get("output_dir", "") or ""
        if not output_dir:
            continue

        # Skip steps where the output_dir is the whole worktree or work_dir only
        if output_dir in (".", "${{ context.work_dir }}") or output_dir.strip("/") == "":
            continue
        if not _has_iteration_scoping(output_dir):
            continue

        skill_cmd = (step.with_args or {}).get("skill_command", "") or ""
        if not skill_cmd:
            continue

        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue

        skill_md_path = _resolve_skill_md(skill_name, resolver=ctx.skill_resolver)
        if skill_md_path is None:
            continue

        try:
            content = skill_md_path.read_text(encoding="utf-8")
        except OSError:
            continue

        if has_dynamic_write_path(content):
            continue

        declared_paths = extract_write_path_declarations(content)
        if not declared_paths:
            continue

        static_base = _static_base_prefix(output_dir)

        for declared in declared_paths:
            normalised_declared = _normalise_path("{{AUTOSKILLIT_TEMP}}/" + declared)
            normalised_base = _normalise_path(static_base)
            if normalised_base.startswith(normalised_declared):
                # Recipe output_dir is a subdirectory of the SKILL.md declared scope
                # and includes iteration scoping — SKILL.md paths will be blocked
                findings.append(
                    RuleFinding(
                        rule="skill-write-path-recipe-alignment",
                        severity=Severity.ERROR,
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
                break

    return findings
