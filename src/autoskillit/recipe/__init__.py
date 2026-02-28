"""L2 recipe domain — schema, I/O, validation, and contract management."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger, load_yaml

_logger = get_logger(__name__)

from autoskillit.recipe.contracts import (  # noqa: E402
    StaleItem,
    check_contract_staleness,
    generate_recipe_card,
    load_bundled_manifest,
    load_recipe_card,
    validate_recipe_cards,
)
from autoskillit.recipe.io import (  # noqa: E402
    DefaultRecipeRepository,
    find_recipe_by_name,
    iter_steps_with_context,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe.loader import parse_recipe_metadata  # noqa: E402
from autoskillit.recipe.schema import Recipe, RecipeStep  # noqa: E402
from autoskillit.recipe.validator import (  # noqa: E402
    RuleFinding,
    analyze_dataflow,
    run_semantic_rules,
    validate_recipe,
)

__all__ = [
    "Recipe",
    "RecipeStep",
    "StaleItem",
    "RuleFinding",
    "load_recipe",
    "list_recipes",
    "find_recipe_by_name",
    "iter_steps_with_context",
    "validate_recipe",
    "run_semantic_rules",
    "analyze_dataflow",
    "check_contract_staleness",
    "generate_recipe_card",
    "load_bundled_manifest",
    "load_recipe_card",
    "validate_recipe_cards",
    "DefaultRecipeRepository",
    "parse_recipe_metadata",
    "load_and_validate",
    "validate_from_path",
    "list_all",
]


def list_all(project_dir: Path | None = None) -> dict[str, Any]:
    """List all recipes from project and built-in sources.

    Returns:
        {"recipes": list[{"name", "description", "summary"}]}
        Includes "errors" key when recipes fail to parse.
    """
    from autoskillit.recipe.io import format_recipe_list_response as _format_recipe_list_response

    _pdir = project_dir if project_dir is not None else Path.cwd()
    result = list_recipes(_pdir)
    return _format_recipe_list_response(result)


def validate_from_path(path: Path) -> dict[str, Any]:
    """Validate a recipe YAML file at the given path.

    Returns:
        {"valid": bool, "errors": list, "quality": dict, "semantic": list, "contracts": list}
        On file/parse error: {"error": str}
    """
    from autoskillit.core import YAMLError
    from autoskillit.recipe.contracts import load_recipe_card as _load_card
    from autoskillit.recipe.contracts import validate_recipe_cards as _validate_cards
    from autoskillit.recipe.io import _parse_recipe
    from autoskillit.recipe.validator import (
        analyze_dataflow,
    )
    from autoskillit.recipe.validator import (
        build_quality_dict as _build_quality_dict,
    )
    from autoskillit.recipe.validator import (
        compute_recipe_validity as _compute_recipe_validity,
    )
    from autoskillit.recipe.validator import (
        findings_to_dicts as _findings_to_dicts,
    )
    from autoskillit.recipe.validator import run_semantic_rules as _run_semantic
    from autoskillit.recipe.validator import validate_recipe as _validate_struct

    if not path.is_file():
        return {
            "valid": False,
            "findings": [{"error": f"File not found: {path}"}],
        }

    try:
        data = load_yaml(path)
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

    recipe = _parse_recipe(data)
    errors = _validate_struct(recipe)
    report = analyze_dataflow(recipe)
    semantic_findings = _run_semantic(recipe)

    quality = _build_quality_dict(report)
    semantic = _findings_to_dicts(semantic_findings)

    contract_findings: list[dict[str, Any]] = []
    recipes_dir = path.parent
    recipe_name = path.stem
    contract = _load_card(recipe_name, recipes_dir)
    if contract:
        contract_findings = _validate_cards(recipe, contract)

    valid = _compute_recipe_validity(errors, semantic_findings, contract_findings)

    return {
        "valid": valid,
        "errors": errors,
        "quality": quality,
        "findings": semantic,
        "contracts": contract_findings,
    }


def load_and_validate(
    name: str,
    project_dir: Path | None = None,
    *,
    suppressed: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Load a recipe by name and run full validation.

    Args:
        name: Recipe name (without .yaml extension).
        project_dir: Directory to search (defaults to cwd).
        suppressed: Recipe names for which the version-outdated rule is silenced.

    Returns:
        {"content": str, "suggestions": list, "valid": bool}
        On not-found: {"error": str}
    """
    from autoskillit.core import YAMLError
    from autoskillit.recipe.contracts import check_contract_staleness as _check_staleness
    from autoskillit.recipe.contracts import load_recipe_card as _load_card
    from autoskillit.recipe.contracts import stale_to_suggestions as _stale_to_suggestions
    from autoskillit.recipe.contracts import validate_recipe_cards as _validate_cards
    from autoskillit.recipe.io import _parse_recipe
    from autoskillit.recipe.validator import (
        compute_recipe_validity as _compute_recipe_validity,
    )
    from autoskillit.recipe.validator import (
        filter_version_rule as _filter_version_rule,
    )
    from autoskillit.recipe.validator import (
        findings_to_dicts as _findings_to_dicts,
    )
    from autoskillit.recipe.validator import run_semantic_rules as _run_semantic
    from autoskillit.recipe.validator import validate_recipe as _validate_struct

    _pdir = project_dir if project_dir is not None else Path.cwd()
    match = find_recipe_by_name(name, _pdir)
    if match is None:
        return {"error": f"No recipe named '{name}' found"}

    content = match.path.read_text()
    suggestions: list[dict[str, Any]] = []
    valid = True

    try:
        data = load_yaml(content)
        if isinstance(data, dict) and "steps" in data:
            recipe = _parse_recipe(data)
            errors = _validate_struct(recipe)
            semantic_findings = _run_semantic(recipe)
            semantic_suggestions = _findings_to_dicts(semantic_findings)

            _suppressed = suppressed or []
            if name in _suppressed:
                semantic_suggestions = _filter_version_rule(semantic_suggestions)
            suggestions.extend(semantic_suggestions)

            recipes_dir = _pdir / ".autoskillit" / "recipes"
            contract = _load_card(name, recipes_dir)
            contract_findings: list[dict[str, Any]] = []
            if contract:
                contract_findings = _validate_cards(recipe, contract)
                suggestions.extend(contract_findings)
                stale = _check_staleness(contract)
                suggestions.extend(_stale_to_suggestions(stale))

            valid = _compute_recipe_validity(errors, semantic_findings, contract_findings)
    except YAMLError as exc:
        _logger.warning("Recipe YAML parse error", name=name, exc_info=True)
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"YAML parse error: {exc}",
            }
        )
        valid = False
    except ValueError as exc:
        _logger.warning("Recipe structure invalid", name=name, exc_info=True)
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"Invalid recipe structure: {exc}",
            }
        )
        valid = False
    except (FileNotFoundError, OSError) as exc:
        _logger.warning("Recipe file not found or unreadable", name=name, exc_info=True)
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"File error: {exc}",
            }
        )
        valid = False

    return {"content": content, "suggestions": suggestions, "valid": valid}
