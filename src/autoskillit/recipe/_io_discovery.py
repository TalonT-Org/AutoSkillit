"""Private recipe discovery collection and parse caching."""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from pathlib import Path, PurePosixPath

from autoskillit.core import LoadReport, LoadResult, RecipeSource, get_logger
from autoskillit.recipe._io_loading import load_recipe_dict_with_declarations
from autoskillit.recipe.schema import Recipe, RecipeInfo

logger = get_logger(__name__)


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


def is_recipe_scan_path(rel_to_root: PurePosixPath) -> bool:
    """Return whether a recipe-relative path has the live discovery shape."""
    if len(rel_to_root.parts) == 1:
        return "." in RECIPE_SCAN_DIRS
    return len(rel_to_root.parts) == 2 and rel_to_root.parts[0] in RECIPE_SCAN_DIRS


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
) -> tuple[Recipe, str]:
    """Parse one recipe for a metadata fingerprint supplied by its enumerator."""
    from autoskillit.recipe.io import _parse_recipe  # noqa: PLC0415

    raw = path.read_text(encoding="utf-8")
    data, declared_data = load_recipe_dict_with_declarations(path, raw_text=raw)
    return _parse_recipe(data, declared_data=declared_data), raw


def collect_recipes_from_candidates(
    project_base: Path,
    project_files: Iterable[Path],
    builtin_base: Path,
    builtin_files: Iterable[Path],
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
