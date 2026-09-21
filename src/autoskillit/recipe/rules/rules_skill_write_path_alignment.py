"""Semantic rule: SKILL.md declared write scope must align with recipe output_dir.

The skill's declared write_paths bound a recipe output_dir. An iteration-scoped
output_dir also requires the skill's prose write instructions to use the dynamic
write prefix so the agent can target the narrowed directory.
"""

from __future__ import annotations

from pathlib import Path

import regex as re

from autoskillit.core import Severity, destination_location, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import _resolve_skill_md
from autoskillit.recipe._skill_placeholder_parser import (
    extract_write_path_declarations,
    has_dynamic_write_path,
)
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.workspace import parse_frontmatter_content

logger = get_logger(__name__)

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


def _declared_boundary_error(content: str, output_dir: str, project_dir: Path) -> str | None:
    parsed = parse_frontmatter_content(content)
    if not parsed.is_valid or parsed.write_paths is None:
        return None
    static_base = _static_base_prefix(output_dir)
    if not static_base or "${{" in static_base:
        return None
    temp_root = project_dir / ".autoskillit" / "temp"

    def location(path: str) -> Path:
        expanded = path.replace("{{AUTOSKILLIT_TEMP}}", str(temp_root))
        candidate = Path(expanded)
        return destination_location(
            candidate if candidate.is_absolute() else project_dir / candidate
        )

    output_location = location(static_base)
    if any(output_location.is_relative_to(location(path)) for path in parsed.write_paths):
        return None
    return f"recipe output_dir {output_dir!r} is outside the skill's declared write_paths"


@semantic_rule(
    name="skill-write-path-recipe-alignment",
    description=(
        "A SKILL.md's declared write scope does not match the recipe step's output_dir. "
        "The output_dir must remain inside write_paths, and prose write instructions "
        "must respect an iteration-scoped output_dir."
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
        skill_cmd = (step.with_args or {}).get("skill_command", "") or ""
        if not skill_cmd:
            continue

        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue

        skill_md_path = _resolve_skill_md(
            skill_name,
            project_root=ctx.project_dir,
            resolver=ctx.skill_resolver,
        )
        if skill_md_path is None:
            continue

        try:
            content = skill_md_path.read_text(encoding="utf-8")
        except OSError:
            logger.debug("Could not read SKILL.md for %s at %s", skill_name, skill_md_path)
            continue

        boundary_error = _declared_boundary_error(
            content, output_dir, ctx.project_dir or skill_md_path.parent.parent
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
