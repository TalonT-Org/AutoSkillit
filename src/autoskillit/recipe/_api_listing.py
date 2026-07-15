"""Recipe API listing — list_all and validate_from_path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.core import LoadResult, SkillLister, YAMLError, get_logger, load_yaml
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._recipe_composition import _prune_skipped_steps
from autoskillit.recipe._recipe_ingredients import RecipeListItem
from autoskillit.recipe._rule_helpers import filter_pruning_false_positives
from autoskillit.recipe.contracts import load_recipe_card, validate_recipe_cards
from autoskillit.recipe.io import (
    RecipeInfo,
    _parse_recipe,
    list_recipes,
    substitute_temp_placeholder,
)
from autoskillit.recipe.registry import RuleFinding
from autoskillit.recipe.validator import (
    build_quality_dict,
    compute_recipe_validity,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe_structure,
)

logger = get_logger(__name__)


def format_recipe_list_response(result: LoadResult[RecipeInfo]) -> dict[str, object]:
    """Build the MCP response dict for the list_recipes tool."""
    items: list[RecipeListItem] = [
        {
            "name": r.name,
            "description": r.description,
            "summary": r.summary,
            "source": r.source.value,
        }
        for r in result.items
    ]
    response: dict[str, object] = {
        "recipes": items,
        "count": len(items),
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return response


def list_all(
    project_dir: Path | None = None,
    *,
    features: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """List all recipes from project and built-in sources.

    Returns:
        {"recipes": list[{"name", "description", "summary"}]}
        Includes "errors" key when recipes fail to parse.
    """
    from autoskillit.core import is_feature_enabled  # noqa: PLC0415
    from autoskillit.recipe.schema import NON_INTERACTIVE_KINDS  # noqa: PLC0415

    _pdir = project_dir if project_dir is not None else Path.cwd()
    _features = features or {}
    fleet_enabled = is_feature_enabled("fleet", _features)
    exclude_kinds = frozenset() if fleet_enabled else NON_INTERACTIVE_KINDS
    result = list_recipes(_pdir, exclude_kinds=exclude_kinds, exclude_dispatch_only=True)
    return format_recipe_list_response(result)


def validate_from_path(
    path: Path,
    temp_dir_relpath: str = ".autoskillit/temp",
    *,
    lister: SkillLister | None = None,
    backend_name: str | None = None,
    ingredient_overrides: dict[str, str] | None = None,
    effective_backend_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate a recipe YAML file at the given path.

    Args:
        path: Path to the recipe YAML file.
        temp_dir_relpath: Relative path to the temp directory used for
            ``{{AUTOSKILLIT_TEMP}}`` substitution. Defaults to
            ``.autoskillit/temp``.

    Returns:
        {"valid": bool, "errors": list, "quality": dict, "semantic": list, "contracts": list}
        On file/parse error: {"error": str}
    """
    if not path.is_file():
        return {
            "valid": False,
            "findings": [{"error": f"File not found: {path}"}],
        }

    try:
        raw_text = path.read_text(encoding="utf-8")
        substituted = substitute_temp_placeholder(raw_text, temp_dir_relpath)
        data = load_yaml(substituted)
    except YAMLError as exc:
        return {
            "valid": False,
            "findings": [{"error": f"YAML parse error: {exc}"}],
        }

    if not isinstance(data, dict):
        return {
            "valid": False,
            "findings": [{"error": "File must contain a YAML mapping"}],
        }

    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()

    from autoskillit.core import SkillResolver as _SkillResolver  # noqa: PLC0415

    _skill_resolver = lister if isinstance(lister, _SkillResolver) else None

    recipe = _parse_recipe(data)
    errors = validate_recipe_structure(recipe)
    _skip_resolutions: dict[str, bool | None] = {}
    _pre_prune_findings: list[RuleFinding] = []
    if ingredient_overrides:
        pre_prune_ctx = make_validation_context(
            recipe,
            available_skills=frozenset(s.name for s in lister.list_all()),
            skill_resolver=_skill_resolver,
            backend_name=backend_name,
            effective_backend_map=effective_backend_map,
        )
        _pre_prune_findings = run_semantic_rules(pre_prune_ctx)
        recipe, _skip_resolutions = _prune_skipped_steps(
            recipe, ingredient_overrides, defer_unresolved=False
        )
    # Auto-derive on_rate_limit from on_context_limit is intentionally NOT
    # run here — validate_from_path is used by recipe validation tests that
    # assert the raw YAML state. The derivation runs only via load_and_validate
    # (production path). See test_rules_rate_limit_parity.py for the
    # behavioral assertion that this code path preserves raw findings.
    known_skills = frozenset(s.name for s in lister.list_all())
    ctx = make_validation_context(
        recipe,
        available_skills=known_skills,
        skill_resolver=_skill_resolver,
        backend_name=backend_name,
        effective_backend_map=effective_backend_map,
    )
    report = ctx.dataflow
    semantic_findings = run_semantic_rules(ctx)
    if _skip_resolutions and any(v is False for v in _skip_resolutions.values()):
        # Note: the capture-inversion-detection rule is intentionally NOT
        # filtered here. Its strict forward-path dominance check (R1) in
        # _check_capture_inversion subsumes what the filter would mask for
        # this rule. The filter remains useful for other rules like
        # dead-output where pruning genuinely introduces new findings.
        semantic_findings = filter_pruning_false_positives(semantic_findings, _pre_prune_findings)

    quality = build_quality_dict(report)
    semantic = findings_to_dicts(semantic_findings)

    contract_findings: list[dict[str, Any]] = []
    recipes_dir = path.parent
    recipe_name = path.stem
    contract = load_recipe_card(recipe_name, recipes_dir)
    if contract:
        contract_findings = validate_recipe_cards(recipe, contract)

    valid = compute_recipe_validity(errors, semantic_findings, contract_findings)

    return {
        "valid": valid,
        "errors": errors,
        "quality": quality,
        "findings": semantic,
        "contracts": contract_findings,
    }
