"""Semantic rules for pack validation in recipe pipelines."""

from __future__ import annotations

from autoskillit.core import PACK_REGISTRY, SKILL_TOOLS, Severity
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


@semantic_rule(
    name="undeclared-pack-requirement",
    description=(
        "Recipes dispatching skills in default-disabled pack categories "
        "must declare those packs in requires_packs"
    ),
    severity=Severity.ERROR,
)
def _check_undeclared_pack_requirement(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag recipes that dispatch skills in default-disabled pack categories without
    declaring the corresponding requires_packs entry.

    Mirrors the pattern of check_requires_features_declared in rules_features.py.
    PACK_REGISTRY keys directly equal CATEGORY_TAGS, so no intermediate mapping
    (like FeatureDef.skill_categories) is needed — a category that appears in
    PACK_REGISTRY with default_enabled=False is exactly a pack that must be declared.
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
        if step.tool not in SKILL_TOOLS:
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
