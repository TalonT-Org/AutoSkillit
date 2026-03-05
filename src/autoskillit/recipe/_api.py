"""Recipe orchestration API: load/validate pipelines, format responses."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass as _dc
from pathlib import Path
from typing import Any

from autoskillit.core import LoadResult, RecipeSource, YAMLError, get_logger, load_yaml, pkg_root
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.contracts import (
    check_contract_staleness,
    load_recipe_card,
    stale_to_suggestions,
    validate_recipe_cards,
)
from autoskillit.recipe.diagrams import (
    check_diagram_staleness,
    diagram_stale_to_suggestions,
    load_recipe_diagram,
)
from autoskillit.recipe.io import (
    RecipeInfo,
    _parse_recipe,
    builtin_recipes_dir,
    find_recipe_by_name,
    list_recipes,
)
from autoskillit.recipe.validator import (
    build_quality_dict,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe,
)

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Stage timing helper
# ---------------------------------------------------------------------------


def _t(label: str, t0: float, name: str) -> float:
    """Log elapsed time for a pipeline stage and return current time.

    Uses structlog at DEBUG level; structlog's processor chain handles level
    filtering without requiring an explicit isEnabledFor() guard.
    """
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _logger.debug("load_recipe_stage", recipe=name, stage=label, elapsed_ms=round(elapsed_ms, 1))
    return time.perf_counter()


# ---------------------------------------------------------------------------
# Top-level result cache
# ---------------------------------------------------------------------------


def _file_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _get_pkg_version() -> str:
    from autoskillit import __version__

    return __version__


@_dc
class _LoadCacheEntry:
    recipe_path: Path
    recipe_mtime: int
    recipe_size: int
    project_dir_mtime: int
    builtin_dir_mtime: int
    pkg_version: str
    result: dict[str, Any]


_LOAD_CACHE: dict[tuple, _LoadCacheEntry] = {}
_LOAD_CACHE_LOCK = threading.Lock()


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
    ctx = make_validation_context(recipe)
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


def load_and_validate(
    name: str,
    project_dir: Path | None = None,
    *,
    suppressed: Sequence[str] | None = None,
    recipe_info: RecipeInfo | None = None,
) -> dict[str, Any]:
    """Load a recipe by name and run full validation.

    Args:
        name: Recipe name (without .yaml extension).
        project_dir: Directory to search (defaults to cwd).
        suppressed: Recipe names for which the version-outdated rule is silenced.
        recipe_info: Optional pre-resolved ``RecipeInfo`` from the repository's
            mtime-cached list. When provided, ``find_recipe_by_name`` is skipped.

    Returns:
        {"content": str, "suggestions": list, "valid": bool}
        On not-found: {"error": str}
    """
    _pdir = project_dir if project_dir is not None else Path.cwd()
    pkg_version = _get_pkg_version()
    project_recipes_dir = _pdir / ".autoskillit" / "recipes"
    _builtin_dir = builtin_recipes_dir()
    cache_key = (name, str(_pdir), tuple(sorted(suppressed)) if suppressed else ())

    with _LOAD_CACHE_LOCK:
        cached = _LOAD_CACHE.get(cache_key)

    if cached is not None and cached.pkg_version == pkg_version:
        pm = _dir_mtime_ns(project_recipes_dir)
        bm = _dir_mtime_ns(_builtin_dir)
        rm = _file_mtime_ns(cached.recipe_path)
        rs = _file_size(cached.recipe_path)
        if (
            pm == cached.project_dir_mtime
            and bm == cached.builtin_dir_mtime
            and rm == cached.recipe_mtime
            and rs == cached.recipe_size
        ):
            _logger.debug("load_recipe_cache_hit", recipe=name)
            return cached.result

    t0 = time.perf_counter()

    # Stage: find recipe
    if recipe_info is not None:
        match: RecipeInfo | None = recipe_info
    else:
        match = find_recipe_by_name(name, _pdir)
    t0 = _t("find_recipe", t0, name)

    if match is None:
        return {"error": f"No recipe named '{name}' found"}

    raw = match.content if match.content is not None else match.path.read_text()
    suggestions: list[dict[str, Any]] = []
    valid = True
    recipe = None

    # Determine recipes_dir from source
    if match.source == RecipeSource.BUILTIN:
        recipes_dir = pkg_root() / "recipes"
    else:
        recipes_dir = _pdir / ".autoskillit" / "recipes"

    try:
        # Stage: yaml parse
        data = load_yaml(raw)
        t0 = _t("yaml_parse", t0, name)

        if isinstance(data, dict) and "steps" in data:
            recipe = _parse_recipe(data)

            # Stage: structural validation
            errors = validate_recipe(recipe)
            t0 = _t("validate_recipe", t0, name)

            # Stage: semantic rules (builds ValidationContext once — shared computation)
            val_ctx = make_validation_context(recipe)
            semantic_findings = run_semantic_rules(val_ctx)
            semantic_suggestions = findings_to_dicts(semantic_findings)
            t0 = _t("semantic_rules", t0, name)

            _suppressed = suppressed or []
            if name in _suppressed:
                semantic_suggestions = filter_version_rule(semantic_suggestions)
            suggestions.extend(semantic_suggestions)

            # Stage: contract card
            contract = load_recipe_card(name, recipes_dir)
            contract_findings: list[dict[str, Any]] = []
            if contract:
                contract_findings = validate_recipe_cards(recipe, contract)
                suggestions.extend(contract_findings)
            t0 = _t("contract_card", t0, name)

            # Stage: staleness check
            if contract:
                staleness_cache_path = (
                    _pdir / ".autoskillit" / "temp" / "recipe_staleness_cache.json"
                )
                stale = check_contract_staleness(
                    contract, recipe_path=match.path, cache_path=staleness_cache_path
                )
                suggestions.extend(stale_to_suggestions(stale))
            t0 = _t("staleness_check", t0, name)

            # Stage: diagram
            if check_diagram_staleness(name, recipes_dir, match.path):
                suggestions.extend(diagram_stale_to_suggestions(name))
            t0 = _t("diagram", t0, name)

            valid = compute_recipe_validity(errors, semantic_findings, contract_findings)
        else:
            t0 = _t("yaml_parse", t0, name)

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

    # Load pre-generated diagram
    diagram: str | None = load_recipe_diagram(name, recipes_dir)

    if recipe is not None and recipe.kitchen_rules:
        result: dict[str, Any] = {
            "content": raw,
            "diagram": diagram,
            "suggestions": suggestions,
            "valid": valid,
            "kitchen_rules": recipe.kitchen_rules,
        }
    else:
        result = {"content": raw, "diagram": diagram, "suggestions": suggestions, "valid": valid}

    # Write to cache (only when recipe was found and fully processed)
    if match is not None:
        entry = _LoadCacheEntry(
            recipe_path=match.path,
            recipe_mtime=_file_mtime_ns(match.path),
            recipe_size=_file_size(match.path),
            project_dir_mtime=_dir_mtime_ns(project_recipes_dir),
            builtin_dir_mtime=_dir_mtime_ns(_builtin_dir),
            pkg_version=pkg_version,
            result=result,
        )
        with _LOAD_CACHE_LOCK:
            _LOAD_CACHE[cache_key] = entry

    return result
