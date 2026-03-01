"""Recipe orchestration API: load/validate pipelines, format responses."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from autoskillit.core import LoadResult, YAMLError, get_logger, load_yaml
from autoskillit.recipe.contracts import (
    check_contract_staleness,
    load_recipe_card,
    stale_to_suggestions,
    validate_recipe_cards,
)
from autoskillit.recipe.io import (
    RecipeInfo,
    _parse_recipe,
    find_recipe_by_name,
    list_recipes,
)
from autoskillit.recipe.validator import (
    analyze_dataflow,
    build_quality_dict,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe,
)

_logger = get_logger(__name__)


def format_recipe_list_response(result: LoadResult[RecipeInfo]) -> dict[str, object]:
    """Build the MCP response dict for the list_recipes tool."""
    items = [
        {"name": r.name, "description": r.description, "summary": r.summary} for r in result.items
    ]
    response: dict[str, object] = {
        "recipes": items,
        "count": len(items),
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return response


def list_all(project_dir: Path | None = None) -> dict[str, Any]:
    """List all recipes from project and built-in sources.

    Returns:
        {"recipes": list[{"name", "description", "summary"}]}
        Includes "errors" key when recipes fail to parse.
    """
    _pdir = project_dir if project_dir is not None else Path.cwd()
    result = list_recipes(_pdir)
    return format_recipe_list_response(result)


def validate_from_path(path: Path) -> dict[str, Any]:
    """Validate a recipe YAML file at the given path.

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
    errors = validate_recipe(recipe)
    report = analyze_dataflow(recipe)
    semantic_findings = run_semantic_rules(recipe)

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
    _pdir = project_dir if project_dir is not None else Path.cwd()
    match = find_recipe_by_name(name, _pdir)
    if match is None:
        return {"error": f"No recipe named '{name}' found"}

    raw = match.content if match.content is not None else match.path.read_text()
    suggestions: list[dict[str, Any]] = []
    valid = True

    try:
        data = load_yaml(raw)
        if isinstance(data, dict) and "steps" in data:
            recipe = _parse_recipe(data)
            errors = validate_recipe(recipe)
            semantic_findings = run_semantic_rules(recipe)
            semantic_suggestions = findings_to_dicts(semantic_findings)

            _suppressed = suppressed or []
            if name in _suppressed:
                semantic_suggestions = filter_version_rule(semantic_suggestions)
            suggestions.extend(semantic_suggestions)

            recipes_dir = _pdir / ".autoskillit" / "recipes"
            contract = load_recipe_card(name, recipes_dir)
            contract_findings: list[dict[str, Any]] = []
            if contract:
                contract_findings = validate_recipe_cards(recipe, contract)
                suggestions.extend(contract_findings)
                cache_path = _pdir / ".autoskillit" / "temp" / "recipe_staleness_cache.json"
                stale = check_contract_staleness(
                    contract, recipe_path=match.path, cache_path=cache_path
                )
                suggestions.extend(stale_to_suggestions(stale))

            valid = compute_recipe_validity(errors, semantic_findings, contract_findings)
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

    return {"content": raw, "suggestions": suggestions, "valid": valid}
