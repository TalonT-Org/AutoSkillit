"""Semantic rules for feature-gated tool and skill references.

Validates that recipe steps do not reference tools or skills belonging to
features that are currently disabled in the project configuration.
"""

from __future__ import annotations

from autoskillit.core import (
    FEATURE_REGISTRY,
    SKILL_TOOLS,
    TOOL_SUBSET_TAGS,
    FeatureDef,
    Severity,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tools_for_feature(fdef: FeatureDef) -> frozenset[str]:
    """Return all MCP tool names that carry at least one of this feature's tool_tags."""
    return frozenset(tool for tool, tags in TOOL_SUBSET_TAGS.items() if fdef.tool_tags & tags)


def _get_disabled_feature_defs(ctx: ValidationContext) -> list[FeatureDef]:
    """Return FeatureDef objects for all features named in ctx.disabled_features."""
    return [fdef for name, fdef in FEATURE_REGISTRY.items() if name in ctx.disabled_features]


def _get_skill_category_map() -> dict[str, frozenset[str]]:
    """Return {skill_name: categories} for all bundled skills (deferred import)."""
    from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

    lister = DefaultSkillResolver()
    return {s.name: s.categories for s in lister.list_all()}


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------


@semantic_rule(
    name="feature-gate-tool-reference",
    description="Steps must not reference tools or skills from disabled features",
    severity=Severity.ERROR,
)
def check_feature_gated_tools(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag steps that reference tools or skills belonging to a disabled feature."""
    if not ctx.disabled_features:
        return []

    disabled_fdefs = _get_disabled_feature_defs(ctx)
    if not disabled_fdefs:
        return []

    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        for fdef in disabled_fdefs:
            # --- Tool check ---
            if step.tool and step.tool in _tools_for_feature(fdef):
                findings.append(
                    RuleFinding(
                        rule="feature-gate-tool-reference",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"step '{step_name}': tool '{step.tool}' belongs to "
                            f"disabled feature '{fdef.name}'. "
                            f"Enable '{fdef.name}' in .autoskillit/config.yaml "
                            f"features to use this tool."
                        ),
                    )
                )

            # --- Skill check (only when the feature gates skill categories) ---
            if step.tool not in SKILL_TOOLS or not fdef.skill_categories:
                continue
            skill_cmd = (step.with_args or {}).get("skill_command", "")
            skill_name = resolve_skill_name(skill_cmd)
            if skill_name is None:
                continue
            category_map = (
                ctx.skill_category_map
                if ctx.skill_category_map is not None
                else _get_skill_category_map()
            )
            categories = category_map.get(skill_name, frozenset())
            if categories & fdef.skill_categories:
                findings.append(
                    RuleFinding(
                        rule="feature-gate-tool-reference",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"step '{step_name}': skill_command '{skill_cmd}' references "
                            f"skill '{skill_name}' which belongs to disabled feature "
                            f"'{fdef.name}'. "
                            f"Enable '{fdef.name}' in .autoskillit/config.yaml "
                            f"features to use this skill."
                        ),
                    )
                )

    return findings
