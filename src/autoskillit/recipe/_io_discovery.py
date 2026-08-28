"""Filesystem-signature caching for recipe discovery."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from stat import S_ISDIR

from autoskillit.core import LoadReport, LoadResult
from autoskillit.recipe.schema import RecipeInfo

_DirectorySignature = tuple[tuple[str, bool, int | None, int | None], ...]
_RecipeInputSignature = tuple[
    tuple[Path, int, int, int, bool, int | None, int | None, int | None], ...
]
_CachedRecipeCollection = tuple[tuple[RecipeInfo, ...], tuple[LoadReport, ...]]
_CandidateEnumerator = Callable[[Path], tuple[Path, ...]]
_CandidateCollector = Callable[
    [Path, Iterable[Path], Path, Iterable[Path]], LoadResult[RecipeInfo]
]


def _recipe_directory_signature(
    source_root: Path,
    scan_dirs: tuple[str, ...],
) -> _DirectorySignature:
    signature: list[tuple[str, bool, int | None, int | None]] = []
    for subdir in scan_dirs:
        directory = source_root / subdir
        try:
            directory_stat = directory.stat()
        except OSError:
            signature.append((subdir, False, None, None))
            continue
        if not S_ISDIR(directory_stat.st_mode):
            signature.append((subdir, False, None, None))
            continue
        signature.append((subdir, True, directory_stat.st_mtime_ns, directory_stat.st_ctime_ns))
    return tuple(signature)


def _enumerate_recipe_candidates_uncached(
    source_root: Path,
    scan_dirs: tuple[str, ...],
) -> tuple[Path, ...]:
    """Return ordered recipe candidates relative to one resolved source root."""
    candidates: list[tuple[tuple[int, str], Path]] = []
    for scan_rank, subdir in enumerate(scan_dirs):
        directory = source_root / subdir
        try:
            for path in directory.iterdir():
                if path.suffix not in (".yaml", ".yml") or not path.is_file():
                    continue
                candidates.append(((scan_rank, path.name), path.relative_to(source_root)))
        except OSError:
            continue
    return tuple(relative_path for _, relative_path in sorted(candidates))


@lru_cache(maxsize=256)
def _cached_recipe_candidates(
    source_identity: Path,
    _directory_signature: _DirectorySignature,
    enumerate_candidates: _CandidateEnumerator,
) -> tuple[Path, ...]:
    return enumerate_candidates(source_identity)


def _recipe_input_signature(
    lexical_base: Path,
    relative_candidates: tuple[Path, ...],
) -> _RecipeInputSignature | None:
    signature: list[tuple[Path, int, int, int, bool, int | None, int | None, int | None]] = []
    for relative_path in relative_candidates:
        yaml_path = lexical_base / relative_path
        try:
            yaml_stat = yaml_path.stat()
        except OSError:
            return None
        json_path = yaml_path.with_suffix(".json")
        try:
            json_stat = json_path.stat()
        except OSError:
            json_stat = None
        # Preserved-mtime detection relies on POSIX utimensat(2) changing ctime on our runners.
        signature.append(
            (
                relative_path,
                yaml_stat.st_mtime_ns,
                yaml_stat.st_ctime_ns,
                yaml_stat.st_size,
                json_stat is not None,
                json_stat.st_mtime_ns if json_stat is not None else None,
                json_stat.st_ctime_ns if json_stat is not None else None,
                json_stat.st_size if json_stat is not None else None,
            )
        )
    return tuple(signature)


def _collect_relative_recipe_candidates(
    project_base: Path,
    project_candidates: tuple[Path, ...],
    builtin_base: Path,
    builtin_candidates: tuple[Path, ...],
    collect_candidates: _CandidateCollector,
) -> _CachedRecipeCollection:
    result = collect_candidates(
        project_base,
        tuple(project_base / relative_path for relative_path in project_candidates),
        builtin_base,
        tuple(builtin_base / relative_path for relative_path in builtin_candidates),
    )
    return tuple(result.items), tuple(result.errors)


@lru_cache(maxsize=256)
def _cached_recipe_collection(
    _project_identity: Path,
    project_base: Path,
    project_directory_signature: _DirectorySignature,
    project_input_signature: _RecipeInputSignature,
    _builtin_identity: Path,
    builtin_base: Path,
    builtin_directory_signature: _DirectorySignature,
    builtin_input_signature: _RecipeInputSignature,
    collect_candidates: _CandidateCollector,
) -> _CachedRecipeCollection:
    del project_directory_signature, builtin_directory_signature
    return _collect_relative_recipe_candidates(
        project_base,
        tuple(entry[0] for entry in project_input_signature),
        builtin_base,
        tuple(entry[0] for entry in builtin_input_signature),
        collect_candidates,
    )


def _discover_recipe_collection(
    project_base: Path,
    builtin_base: Path,
    scan_dirs: tuple[str, ...],
    *,
    enumerate_candidates: _CandidateEnumerator,
    collect_candidates: _CandidateCollector,
) -> _CachedRecipeCollection:
    project_identity = project_base.resolve()
    builtin_identity = builtin_base.resolve()

    for attempt in range(2):
        project_directory_signature = _recipe_directory_signature(project_identity, scan_dirs)
        builtin_directory_signature = _recipe_directory_signature(builtin_identity, scan_dirs)
        project_candidates = _cached_recipe_candidates(
            project_identity,
            project_directory_signature,
            enumerate_candidates,
        )
        builtin_candidates = _cached_recipe_candidates(
            builtin_identity,
            builtin_directory_signature,
            enumerate_candidates,
        )
        project_input_signature = _recipe_input_signature(project_base, project_candidates)
        builtin_input_signature = _recipe_input_signature(builtin_base, builtin_candidates)

        if project_input_signature is None or builtin_input_signature is None:
            _cached_recipe_candidates.cache_clear()
            _cached_recipe_collection.cache_clear()
            if attempt == 0:
                continue
            return _collect_relative_recipe_candidates(
                project_base,
                project_candidates,
                builtin_base,
                builtin_candidates,
                collect_candidates,
            )

        result = _cached_recipe_collection(
            project_identity,
            project_base,
            project_directory_signature,
            project_input_signature,
            builtin_identity,
            builtin_base,
            builtin_directory_signature,
            builtin_input_signature,
            collect_candidates,
        )
        stable = (
            project_directory_signature == _recipe_directory_signature(project_identity, scan_dirs)
            and builtin_directory_signature
            == _recipe_directory_signature(builtin_identity, scan_dirs)
            and project_input_signature
            == _recipe_input_signature(project_base, project_candidates)
            and builtin_input_signature
            == _recipe_input_signature(builtin_base, builtin_candidates)
        )
        if stable:
            return result
        _cached_recipe_candidates.cache_clear()
        _cached_recipe_collection.cache_clear()
        if attempt == 1:
            return result

    raise AssertionError("recipe discovery stability loop exhausted")
