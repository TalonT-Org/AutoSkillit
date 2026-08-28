"""Recipe document loading, discovery collection, and placeholder substitution."""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from autoskillit.core import (
    LoadReport,
    LoadResult,
    RecipeSource,
    fast_loads,
    get_logger,
    load_yaml,
    pkg_root,
)
from autoskillit.recipe._contracts_types import INPUT_REF_RE
from autoskillit.recipe.schema import Recipe, RecipeInfo

logger = get_logger(__name__)

_TEMP_PLACEHOLDER = "{{AUTOSKILLIT_TEMP}}"
_SCRIPTS_PLACEHOLDER = "{{AUTOSKILLIT_SCRIPTS}}"

RECIPE_SCAN_DIRS: tuple[str, ...] = (
    ".",  # root — standard recipes
    "campaigns",  # campaign recipes
    "eval",  # eval recipes
)

NON_RECIPE_DIRS: frozenset[str] = frozenset(
    {
        "contracts",
        "diagrams",
        "examples",
        "experiment-types",
        "methodology-traditions",
        "scripts",
        "sub-recipes",
    }
)


class _RecipeParser(Protocol):
    def __hash__(self) -> int: ...

    def __call__(
        self,
        data: dict[str, Any],
        *,
        declared_data: dict[str, Any] | None = None,
    ) -> Recipe: ...


def is_recipe_scan_path(rel_to_root: PurePosixPath) -> bool:
    """Return whether a recipe-relative path has the live discovery shape."""
    if len(rel_to_root.parts) == 1:
        return "." in RECIPE_SCAN_DIRS
    return len(rel_to_root.parts) == 2 and rel_to_root.parts[0] in RECIPE_SCAN_DIRS


def substitute_temp_placeholder(text: str, temp_dir_relpath: str) -> str:
    """Replace the temp placeholder after rejecting YAML-unsafe path text."""
    if "\n" in temp_dir_relpath or ": " in temp_dir_relpath:
        raise ValueError(f"temp_dir_relpath is YAML-unsafe: {temp_dir_relpath!r}")
    return text.replace(_TEMP_PLACEHOLDER, temp_dir_relpath)


def substitute_scripts_placeholder(text: str) -> str:
    """Replace the scripts placeholder with the bundled recipe scripts path."""
    if _SCRIPTS_PLACEHOLDER not in text:
        return text
    scripts_dir = pkg_root() / "recipes" / "scripts"
    return text.replace(_SCRIPTS_PLACEHOLDER, str(scripts_dir))


def assert_no_raw_placeholders(
    text: str,
    *,
    context: str = "",
    hidden_ingredient_names: frozenset[str] | None = None,
) -> None:
    """Reject unresolved host or hidden-ingredient placeholders at delivery."""
    for placeholder in (_TEMP_PLACEHOLDER, _SCRIPTS_PLACEHOLDER):
        if placeholder in text:
            raise ValueError(
                f"Unresolved {placeholder} in recipe content"
                + (f" ({context})" if context else "")
            )
    if hidden_ingredient_names:
        for match in INPUT_REF_RE.finditer(text):
            name = match.group(1)
            if name in hidden_ingredient_names:
                raise ValueError(
                    f"Unresolved hidden ingredient template ${{{{ inputs.{name} }}}} "
                    "in recipe content" + (f" ({context})" if context else "")
                )


def load_recipe_dict(
    yaml_path: Path,
    *,
    raw_text: str | None = None,
    temp_dir_relpath: str | None = None,
) -> dict[str, Any]:
    """Load an effective recipe mapping, preferring a fresh compiled sibling."""
    effective, _declared = load_recipe_dict_with_declarations(
        yaml_path,
        raw_text=raw_text,
        temp_dir_relpath=temp_dir_relpath,
    )
    return effective


def _substitute_recipe_values(
    value: Any,
    *,
    temp_dir_relpath: str | None,
) -> Any:
    if isinstance(value, str):
        resolved = (
            substitute_temp_placeholder(value, temp_dir_relpath)
            if temp_dir_relpath is not None
            else value
        )
        return substitute_scripts_placeholder(resolved)
    if isinstance(value, dict):
        return {
            key: _substitute_recipe_values(item, temp_dir_relpath=temp_dir_relpath)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _substitute_recipe_values(item, temp_dir_relpath=temp_dir_relpath) for item in value
        ]
    return value


def load_recipe_dict_with_declarations(
    yaml_path: Path,
    *,
    raw_text: str | None = None,
    temp_dir_relpath: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load aligned effective and declared mappings from JSON or YAML."""
    json_path = yaml_path.with_suffix(".json")
    try:
        if json_path.stat().st_mtime_ns >= yaml_path.stat().st_mtime_ns:
            text = json_path.read_text(encoding="utf-8")
            data = fast_loads(text)
            if isinstance(data, dict):
                return (
                    _substitute_recipe_values(
                        data,
                        temp_dir_relpath=temp_dir_relpath,
                    ),
                    data,
                )
            logger.warning(
                "Pre-compiled JSON is not a mapping, falling back to YAML: %s", json_path
            )
    except json.JSONDecodeError:
        logger.warning("Pre-compiled JSON is corrupt, falling back to YAML: %s", json_path)
    except (FileNotFoundError, OSError):
        pass
    if raw_text is None:
        raw_text = yaml_path.read_text(encoding="utf-8")
    data = load_yaml(raw_text)
    if not isinstance(data, dict):
        raise ValueError(f"Recipe file must contain a YAML mapping: {yaml_path}")
    return (
        _substitute_recipe_values(data, temp_dir_relpath=temp_dir_relpath),
        data,
    )


@cache
def _parse_recipe_candidate(
    path: Path,
    _yaml_mtime_ns: int,
    _yaml_ctime_ns: int,
    _yaml_size: int,
    _json_exists: bool,
    _json_mtime_ns: int | None,
    _json_ctime_ns: int | None,
    _json_size: int | None,
    parse_recipe: _RecipeParser,
) -> tuple[Recipe, str]:
    """Parse one recipe for a metadata fingerprint supplied by its enumerator."""
    raw = path.read_text(encoding="utf-8")
    data, declared_data = load_recipe_dict_with_declarations(path, raw_text=raw)
    return parse_recipe(data, declared_data=declared_data), raw


def _collect_recipes_from_candidates(
    project_base: Path,
    project_files: Iterable[Path],
    builtin_base: Path,
    builtin_files: Iterable[Path],
    *,
    parse_recipe: _RecipeParser,
) -> LoadResult[RecipeInfo]:
    """Parse, deduplicate, and report collisions for recipe candidates by tier."""
    items: list[RecipeInfo] = []
    errors: list[LoadReport] = []
    seen: dict[str, RecipeInfo] = {}

    def collect_tier(source: RecipeSource, base: Path, files: Iterable[Path]) -> None:
        ordered: list[tuple[tuple[int, str], Path]] = []
        for path in files:
            try:
                relative_path = path.relative_to(base)
                scan_dir = "." if len(relative_path.parts) == 1 else relative_path.parts[0]
                ordered.append(((RECIPE_SCAN_DIRS.index(scan_dir), path.name), path))
            except Exception as exc:
                logger.warning(
                    "Failed to collect recipe file",
                    path=str(path),
                    error=str(exc),
                    exc_info=True,
                )
                errors.append(LoadReport(path=path, error=str(exc)))

        for _, path in sorted(ordered):
            try:
                yaml_stat = path.stat()
                json_path = path.with_suffix(".json")
                try:
                    json_stat = json_path.stat()
                except OSError:
                    json_stat = None
                # Performance-only metadata cache; exotic same-metadata writes can evade it,
                # but Git enumeration is always rerun.
                recipe, raw = _parse_recipe_candidate(
                    path,
                    yaml_stat.st_mtime_ns,
                    yaml_stat.st_ctime_ns,
                    yaml_stat.st_size,
                    json_stat is not None,
                    json_stat.st_mtime_ns if json_stat is not None else None,
                    json_stat.st_ctime_ns if json_stat is not None else None,
                    json_stat.st_size if json_stat is not None else None,
                    parse_recipe,
                )
                if not recipe.name:
                    continue

                incumbent = seen.get(recipe.name)
                if incumbent is not None:
                    if incumbent.source == source:
                        errors.append(
                            LoadReport(
                                path=path,
                                error=(
                                    f"Recipe name {recipe.name!r} is declared by both "
                                    f"{incumbent.path} and {path}; same-tier duplicate names "
                                    "have no defined precedence."
                                ),
                            )
                        )
                    else:
                        logger.info(
                            "recipe_name_shadowed",
                            name=recipe.name,
                            winner=str(incumbent.path),
                            shadowed=str(path),
                        )
                    continue

                from autoskillit.recipe.staleness_cache import (  # noqa: PLC0415
                    compute_recipe_hash as _crh,
                )

                recipe_info = RecipeInfo(
                    name=recipe.name,
                    description=recipe.description,
                    source=source,
                    path=path,
                    summary=recipe.summary,
                    version=recipe.version,
                    recipe_version=recipe.recipe_version,
                    content_hash=_crh(path),
                    content=raw,
                    kind=recipe.kind,
                    experimental=recipe.experimental,
                    requires_packs=list(recipe.requires_packs),
                    dispatch_only=recipe.dispatch_only,
                )
                seen[recipe.name] = recipe_info
                items.append(recipe_info)
            except Exception as exc:
                logger.warning(
                    "Failed to load recipe file",
                    path=str(path),
                    error=str(exc),
                    exc_info=True,
                )
                errors.append(LoadReport(path=path, error=str(exc)))

    collect_tier(RecipeSource.PROJECT, project_base, project_files)
    collect_tier(RecipeSource.BUILTIN, builtin_base, builtin_files)
    return LoadResult(items=items, errors=errors)
