"""L2 recipe domain — schema, I/O, validation, and contract management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.recipe.contracts import (
    StaleItem,
    check_contract_staleness,
    generate_recipe_card,
    load_bundled_manifest,
    load_recipe_card,
    validate_recipe_cards,
)
from autoskillit.recipe.io import (
    DefaultRecipeRepository,
    find_recipe_by_name,
    iter_steps_with_context,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe.loader import parse_recipe_metadata
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import (
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
        {"count": int, "recipes": list[{"name", "description", "summary"}]}
    """
    _pdir = project_dir if project_dir is not None else Path.cwd()
    result = list_recipes(_pdir)
    recipes_list = [
        {"name": r.name, "description": r.description, "summary": r.summary} for r in result.items
    ]
    return {"count": len(recipes_list), "recipes": recipes_list}


def validate_from_path(path: Path) -> dict[str, Any]:
    """Validate a recipe YAML file at the given path.

    Returns:
        {"valid": bool, "findings": list}
        On file/parse error: {"valid": False, "findings": [str, ...]}
    """
    from autoskillit.core import YAMLError
    from autoskillit.recipe.contracts import load_recipe_card as _load_card
    from autoskillit.recipe.contracts import validate_recipe_cards as _validate_cards
    from autoskillit.recipe.validator import compute_recipe_validity, findings_to_dicts
    from autoskillit.recipe.validator import run_semantic_rules as _run_semantic

    if not path.is_file():
        return {"valid": False, "findings": [f"File not found: {path}"]}

    try:
        recipe = load_recipe(path)
    except YAMLError as exc:
        return {"valid": False, "findings": [f"YAML parse error: {exc}"]}
    except ValueError as exc:
        return {"valid": False, "findings": [f"Invalid recipe structure: {exc}"]}

    errors = validate_recipe(recipe)
    semantic_findings = _run_semantic(recipe)

    contract_findings: list[dict[str, Any]] = []
    contract = _load_card(path.stem, path.parent)
    if contract:
        contract_findings = _validate_cards(recipe, contract)

    valid = compute_recipe_validity(errors, semantic_findings, contract_findings)
    findings: list[Any] = (
        list(errors) + findings_to_dicts(semantic_findings) + list(contract_findings)
    )

    return {"valid": valid, "findings": findings}


def load_and_validate(
    name: str,
    project_dir: Path | None = None,
    *,
    suppressed: list[str] | None = None,
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
    from autoskillit.core import YAMLError, load_yaml
    from autoskillit.recipe.contracts import check_contract_staleness as _check_staleness
    from autoskillit.recipe.contracts import load_recipe_card as _load_card
    from autoskillit.recipe.contracts import stale_to_suggestions
    from autoskillit.recipe.contracts import validate_recipe_cards as _validate_cards
    from autoskillit.recipe.io import _parse_recipe
    from autoskillit.recipe.validator import (
        compute_recipe_validity,
        filter_version_rule,
        findings_to_dicts,
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
            semantic_suggestions = findings_to_dicts(semantic_findings)

            _suppressed = suppressed or []
            if name in _suppressed:
                semantic_suggestions = filter_version_rule(semantic_suggestions)
            suggestions.extend(semantic_suggestions)

            recipes_dir = _pdir / ".autoskillit" / "recipes"
            contract = _load_card(name, recipes_dir)
            contract_findings: list[dict[str, Any]] = []
            if contract:
                contract_findings = _validate_cards(recipe, contract)
                suggestions.extend(contract_findings)
                stale = _check_staleness(contract)
                suggestions.extend(stale_to_suggestions(stale))

            valid = compute_recipe_validity(errors, semantic_findings, contract_findings)
    except YAMLError:
        suggestions.append(
            {
                "rule": "yaml-error",
                "severity": "error",
                "step": "(load)",
                "message": "YAML parse error",
            }
        )
        valid = False

    return {"content": content, "suggestions": suggestions, "valid": valid}
