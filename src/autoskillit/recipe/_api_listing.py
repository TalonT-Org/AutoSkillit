"""Recipe API listing — list_all and validate_from_path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.core import LoadResult, SkillLister, YAMLError, get_logger, load_yaml
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._recipe_ingredients import RecipeListItem
from autoskillit.recipe.contracts import load_recipe_card, validate_recipe_cards
from autoskillit.recipe.io import (
    RecipeInfo,
    _parse_recipe,
    list_recipes,
    substitute_temp_placeholder,
)
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
    known_skills = frozenset(s.name for s in lister.list_all())
    ctx = make_validation_context(
        recipe, available_skills=known_skills, skill_resolver=_skill_resolver
    )
    report = ctx.dataflow
    semantic_findings = run_semantic_rules(ctx)

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
