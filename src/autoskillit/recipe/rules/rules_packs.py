"""Semantic rules for pack validation in recipe pipelines."""

from __future__ import annotations

from autoskillit.core import (
    CATEGORY_TAGS,
    EXPLORATION_TOOLS,
    KITCHEN_GATED_TOOLS,
    PACK_REGISTRY,
    SKILL_TOOLS,
    TOOL_SUBSET_TAGS,
    Severity,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._skill_helpers import _get_skill_category_map
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule


@semantic_rule(
    name="unknown-required-pack",
    description="Pack name in requires_packs is not in PACK_REGISTRY",
    severity=Severity.ERROR,
)
def _check_unknown_required_pack(ctx: ValidationContext) -> list[RuleFinding]:
    findings = []
    seen_reported: set[str] = set()
    for pack_name in ctx.recipe.requires_packs:
        if pack_name not in PACK_REGISTRY and pack_name not in seen_reported:
            seen_reported.add(pack_name)
            findings.append(
                make_finding(
                    rule_name="unknown-required-pack",
                    step_name="(top-level)",
                    message=(
                        f"Pack {pack_name!r} in requires_packs is not in PACK_REGISTRY. "
                        f"Known packs: {sorted(PACK_REGISTRY)}"
                    ),
                )
            )
    return findings


def _food_truck_can_call(tool: str, declared_packs: frozenset[str]) -> bool:
    """Whether a food truck dispatched with *declared_packs* exposes *tool*.

    Non-empty packs enable ``kitchen-core`` plus each declared pack. Empty packs
    enable the ``kitchen`` tag, including exploration tools.
    """
    if not declared_packs:
        return tool in KITCHEN_GATED_TOOLS | EXPLORATION_TOOLS
    return bool(TOOL_SUBSET_TAGS[tool] & (declared_packs | {"kitchen-core"}))


@semantic_rule(
    name="undeclared-pack-requirement",
    description=(
        "Recipes must declare in requires_packs every pack gating a dispatched "
        "skill category or a direct tool step's food-truck visibility"
    ),
    severity=Severity.ERROR,
)
def _check_undeclared_pack_requirement(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag dispatched skills in undeclared default-disabled pack categories.

    Direct tools must be visible under the exact non-empty ``requires_packs``
    allowlist, with ``kitchen-core`` implicit. Empty packs expose kitchen-tagged
    tools, including exploration tools. ``_food_truck_can_call`` mirrors
    ``_apply_session_type_visibility``: each declared pack is enabled separately,
    so any matching tag suffices.

    Only the static pack axis is checked. Config-disabled subsets and feature
    tags suppressed by ``_suppress_disabled_feature_tags`` are covered separately
    by ``_check_subset_disabled_tool`` and ``check_requires_features_declared``.
    """
    category_map = (
        ctx.skill_category_map if ctx.skill_category_map is not None else _get_skill_category_map()
    )
    declared_packs = frozenset(ctx.recipe.requires_packs)
    default_disabled = frozenset(
        name for name, pdef in PACK_REGISTRY.items() if not pdef.default_enabled
    )

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool is None:
            continue
        if step.tool not in SKILL_TOOLS:
            tool_packs = TOOL_SUBSET_TAGS.get(step.tool, frozenset()) & CATEGORY_TAGS
            if tool_packs and not _food_truck_can_call(step.tool, declared_packs):
                findings.append(
                    make_finding(
                        rule_name="undeclared-pack-requirement",
                        step_name=step_name,
                        message=(
                            f"step '{step_name}': tool '{step.tool}' is not callable in this "
                            f"recipe's food-truck session. Declare one of its packs "
                            f"{sorted(tool_packs)} in requires_packs."
                        ),
                    )
                )
            continue
        skill_cmd = (step.with_args or {}).get("skill_command") or ""
        skill_name = resolve_skill_name(skill_cmd)
        if skill_name is None:
            continue
        categories = category_map.get(skill_name, frozenset())
        for cat in sorted(categories & default_disabled):
            if cat not in declared_packs:
                findings.append(
                    make_finding(
                        rule_name="undeclared-pack-requirement",
                        step_name=step_name,
                        message=(
                            f"step '{step_name}': skill_command '{skill_cmd}' references "
                            f"skill '{skill_name}' which belongs to default-disabled pack "
                            f"'{cat}'. Add '{cat}' to this recipe's requires_packs so "
                            f"init_session can enable the pack gate."
                        ),
                    )
                )
    return findings
