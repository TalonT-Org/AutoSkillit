"""Semantic rules for pack validation in recipe pipelines."""

from __future__ import annotations

from autoskillit.core import PACK_REGISTRY, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule


@semantic_rule(
    name="unknown-required-pack",
    description="Pack name in requires_packs is not in PACK_REGISTRY",
    severity=Severity.WARNING,
)
def _check_unknown_required_pack(ctx: ValidationContext) -> list[RuleFinding]:
    findings = []
    seen_reported: set[str] = set()
    for pack_name in ctx.recipe.requires_packs:
        if pack_name not in PACK_REGISTRY and pack_name not in seen_reported:
            seen_reported.add(pack_name)
            findings.append(
                RuleFinding(
                    rule="unknown-required-pack",
                    severity=Severity.WARNING,
                    step_name="(top-level)",
                    message=(
                        f"Pack {pack_name!r} in requires_packs is not in PACK_REGISTRY. "
                        f"Known packs: {sorted(PACK_REGISTRY)}"
                    ),
                )
            )
    return findings
