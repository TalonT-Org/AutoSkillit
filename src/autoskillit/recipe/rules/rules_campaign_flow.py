"""Campaign flow control rules: gates, paths, refs, version, skip-when."""

from __future__ import annotations

from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import DispatchGateType, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _load_dispatch_target
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import CAMPAIGN_REF_RE, RecipeKind

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)

_VALID_GATE_TYPES: frozenset[DispatchGateType] = frozenset({DispatchGateType.CONFIRM})

_INPUTS_REF_RE = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}")
_ANY_TEMPLATE_REF_RE = re.compile(r"\$\{\{\s*(?:inputs|campaign)\.\w+\s*\}\}")
_SKIP_EXPR_RE = re.compile(r"^.+\s+(?:==|!=)\s+.+$")


def _build_ancestors(name: str, adjacency: dict[str, list[str]]) -> set[str]:
    """Transitive closure of depends_on for a given dispatch name."""
    ancestors: set[str] = set()
    queue = list(adjacency.get(name, []))
    while queue:
        dep = queue.pop()
        if dep not in ancestors:
            ancestors.add(dep)
            queue.extend(adjacency.get(dep, []))
    return ancestors


@semantic_rule(
    name="campaign-ingredient-refs-have-prior-capture",
    description="${{ campaign.key }} in ingredients must be captured by an ancestor dispatch",
    severity=Severity.ERROR,
)
def _check_campaign_ingredient_refs_have_prior_capture(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    adjacency = {d.name: list(d.depends_on) for d in ctx.recipe.dispatches}
    dispatch_by_name = {d.name: d for d in ctx.recipe.dispatches}
    findings = []
    for d in ctx.recipe.dispatches:
        ancestors = _build_ancestors(d.name, adjacency)
        available_captures: set[str] = set()
        for ancestor_name in ancestors:
            ancestor = dispatch_by_name.get(ancestor_name)
            if ancestor:
                available_captures.update(ancestor.capture.keys())
        for ing_key, ing_val in d.ingredients.items():
            if not isinstance(ing_val, str):
                continue
            for ref in CAMPAIGN_REF_RE.findall(ing_val):
                if ref not in available_captures:
                    findings.append(
                        RuleFinding(
                            rule="campaign-ingredient-refs-have-prior-capture",
                            severity=Severity.ERROR,
                            step_name="(top-level)",
                            message=(
                                f"Dispatch {d.name!r} ingredient {ing_key!r} references "
                                f"${{{{ campaign.{ref} }}}} but no ancestor dispatch "
                                f"(via depends_on) captures {ref!r}. "
                                f"Available captured keys: {sorted(available_captures)}"
                            ),
                        )
                    )
    return findings


@semantic_rule(
    name="autoskillit-version-compatible",
    description="Campaign recipe version requirement must be satisfied by installed version",
    severity=Severity.WARNING,
)
def _check_autoskillit_version_compatible(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    if not ctx.recipe.version:
        return []
    try:
        from importlib.metadata import version  # noqa: PLC0415

        from packaging.version import Version  # noqa: PLC0415

        installed = Version(version("autoskillit"))
        required = Version(ctx.recipe.version)
        if required > installed:
            return [
                RuleFinding(
                    rule="autoskillit-version-compatible",
                    severity=Severity.WARNING,
                    step_name="(top-level)",
                    message=(
                        f"Campaign requires autoskillit>={ctx.recipe.version} "
                        f"but installed version is {installed}."
                    ),
                )
            ]
    except Exception:
        logger.warning("autoskillit_version_check_failed", exc_info=True)
    return []


@semantic_rule(
    name="gate-dispatch-valid-type",
    description="gate value must be 'confirm'",
    severity=Severity.ERROR,
)
def _check_gate_dispatch_valid_type(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate is None:
            continue
        if d.gate not in _VALID_GATE_TYPES:
            findings.append(
                RuleFinding(
                    rule="gate-dispatch-valid-type",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has gate={d.gate!r} which is not a valid "
                        f"gate type. Supported types: {sorted(_VALID_GATE_TYPES)}"
                    ),
                )
            )
    return findings


@semantic_rule(
    name="gate-dispatch-has-message",
    description="A gate dispatch must have a non-empty message",
    severity=Severity.ERROR,
)
def _check_gate_dispatch_has_message(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate is None:
            continue
        if not (d.message or "").strip():
            findings.append(
                RuleFinding(
                    rule="gate-dispatch-has-message",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has gate={d.gate!r} but 'message' is empty. "
                        "A non-empty message is required for gate dispatches."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="gate-dispatch-no-recipe",
    description="A gate dispatch must not specify recipe or task",
    severity=Severity.ERROR,
)
def _check_gate_dispatch_no_recipe(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate is None:
            continue
        if d.recipe or d.task:
            findings.append(
                RuleFinding(
                    rule="gate-dispatch-no-recipe",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has gate={d.gate!r} but also specifies "
                        f"recipe={d.recipe!r} or task={d.task!r}. "
                        "Gate dispatches must not specify recipe or task."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="campaign-path-coherence",
    description=(
        "Detects dispatches that invoke worktree-creating recipes "
        "without re-capturing worktree_path"
    ),
    severity=Severity.ERROR,
)
def _check_campaign_path_coherence(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        for step in target.steps.values():
            if step.capture and "worktree_path" in step.capture:
                if "worktree_path" not in d.capture:
                    findings.append(
                        RuleFinding(
                            rule="campaign-path-coherence",
                            severity=Severity.ERROR,
                            step_name="(top-level)",
                            message=(
                                f"Dispatch '{d.name}' invokes recipe '{d.recipe}' which "
                                f"captures worktree_path at step '{step.name}', but the "
                                f"dispatch does not re-capture worktree_path. Downstream "
                                f"dispatches will receive a stale worktree path."
                            ),
                        )
                    )
                break
    return findings


@semantic_rule(
    name="campaign-path-type-enforce",
    description=(
        "Validates that worktree_relative_path ingredients have "
        "a corresponding worktree_path anchor"
    ),
    severity=Severity.ERROR,
)
def _check_campaign_path_type_enforce(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        for key, ing in target.ingredients.items():
            if getattr(ing, "type", None) == "worktree_relative_path":
                if "worktree_path" not in d.ingredients:
                    findings.append(
                        RuleFinding(
                            rule="campaign-path-type-enforce",
                            severity=Severity.ERROR,
                            step_name="(top-level)",
                            message=(
                                f"Dispatch '{d.name}' provides ingredient '{key}' "
                                f"(type: worktree_relative_path) without a corresponding "
                                f"worktree_path anchor."
                            ),
                        )
                    )
    return findings


@semantic_rule(
    name="gate-dispatch-no-capture",
    description="A gate dispatch must not specify capture",
    severity=Severity.ERROR,
)
def _check_gate_dispatch_no_capture(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate is None:
            continue
        if d.capture:
            findings.append(
                RuleFinding(
                    rule="gate-dispatch-no-capture",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has gate={d.gate!r} but also specifies "
                        f"capture. Gate dispatches produce no L3 session output."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="dispatch-skip-when-valid-expression",
    description="skip_when expressions must reference valid campaign inputs or ancestor captures",
    severity=Severity.ERROR,
)
def _check_dispatch_skip_when_valid(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    adjacency = {d.name: list(d.depends_on) for d in ctx.recipe.dispatches}
    dispatch_by_name = {d.name: d for d in ctx.recipe.dispatches}
    campaign_ingredients = set(ctx.recipe.ingredients.keys())

    for d in ctx.recipe.dispatches:
        if not d.skip_when:
            continue

        for ref in _INPUTS_REF_RE.findall(d.skip_when):
            if ref not in campaign_ingredients:
                findings.append(
                    RuleFinding(
                        rule="dispatch-skip-when-valid-expression",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} skip_when references "
                            f"${{{{ inputs.{ref} }}}} but {ref!r} is not a declared "
                            f"campaign ingredient. Available: {sorted(campaign_ingredients)}"
                        ),
                    )
                )

        ancestors = _build_ancestors(d.name, adjacency)
        available_captures: set[str] = set()
        for ancestor_name in ancestors:
            ancestor = dispatch_by_name.get(ancestor_name)
            if ancestor:
                available_captures.update(ancestor.capture.keys())

        for ref in CAMPAIGN_REF_RE.findall(d.skip_when):
            if ref not in available_captures:
                findings.append(
                    RuleFinding(
                        rule="dispatch-skip-when-valid-expression",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} skip_when references "
                            f"${{{{ campaign.{ref} }}}} but no ancestor dispatch "
                            f"(via depends_on) captures {ref!r}. "
                            f"Available captured keys: {sorted(available_captures)}"
                        ),
                    )
                )

        normalized = _ANY_TEMPLATE_REF_RE.sub("X", d.skip_when).strip()
        if not _SKIP_EXPR_RE.match(normalized):
            findings.append(
                RuleFinding(
                    rule="dispatch-skip-when-valid-expression",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} skip_when expression has invalid format: "
                        f"{d.skip_when!r}. Expected: '<lhs> == <rhs>' or '<lhs> != <rhs>'."
                    ),
                )
            )
    return findings
