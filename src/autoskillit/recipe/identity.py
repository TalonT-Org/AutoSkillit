"""Recipe identity hashing — content and composite fingerprints, query and re-run detection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger
from autoskillit.recipe.contracts import compute_skill_hash, resolve_skill_name
from autoskillit.recipe.io import find_sub_recipe_by_name
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.staleness_cache import compute_recipe_hash

logger = get_logger(__name__)


def compute_composite_hash(
    recipe_path: Path,
    recipe: Recipe,
    *,
    skills_dir: Path,
    project_dir: Path,
    _seen: frozenset[Path] | None = None,
) -> str:
    """Compute a composite hash covering the recipe, referenced skills, and sub-recipes.

    The hash is deterministic: skill and sub-recipe names are sorted before
    hashing. Returns "sha256:<64-hex>".
    """
    if _seen is None:
        _seen = frozenset()

    hasher = hashlib.sha256()

    hasher.update(b"autoskillit-composite-v1\n")

    hasher.update(recipe_path.read_bytes())

    skill_names: set[str] = set()
    for step in recipe.steps.values():
        if step.tool == "run_skill" and step.message:
            name = resolve_skill_name(step.message)
            if name:
                skill_names.add(name)

    for name in sorted(skill_names):
        h = compute_skill_hash(name, skills_dir=skills_dir)
        hasher.update(f"skill:{name}:{h}\n".encode())

    sub_names: set[str] = set()
    for step in recipe.steps.values():
        if step.sub_recipe:
            sub_names.add(step.sub_recipe)

    for name in sorted(sub_names):
        sub_path = find_sub_recipe_by_name(name, project_dir)
        if sub_path and sub_path.is_file():
            resolved = sub_path.resolve()
            if resolved in _seen:
                logger.debug("cycle_detected_in_sub_recipe", path=str(sub_path))
                continue
            sub_recipe = _load_sub_recipe_for_hash(sub_path)
            if sub_recipe is not None:
                sub_hash = compute_composite_hash(
                    sub_path,
                    sub_recipe,
                    skills_dir=skills_dir,
                    project_dir=project_dir,
                    _seen=_seen | {resolved},
                )
            else:
                sub_hash = compute_recipe_hash(sub_path)
            hasher.update(f"sub:{name}:{sub_hash}\n".encode())

    return "sha256:" + hasher.hexdigest()


def _load_sub_recipe_for_hash(path: Path) -> Recipe | None:
    """Lightweight recipe load for hash computation — no validation, no blocks."""
    from autoskillit.recipe.io import _parse_recipe  # noqa: PLC0415

    try:
        from autoskillit.core import load_yaml  # noqa: PLC0415

        data = load_yaml(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "steps" in data:
            return _parse_recipe(data)
    except Exception:
        logger.debug("sub_recipe_load_for_hash_failed", path=str(path), exc_info=True)
    return None


def find_prior_runs(
    sessions_jsonl: Path,
    *,
    composite_hash: str = "",
    recipe_name: str = "",
) -> list[dict[str, Any]]:
    """Return sessions.jsonl entries matching the given recipe identity.

    Filters by composite_hash if provided, then by recipe_name.
    Returns matches sorted by timestamp descending. Skips malformed lines.
    """
    if not sessions_jsonl.is_file():
        return []
    matches: list[dict[str, Any]] = []
    try:
        lines = sessions_jsonl.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        logger.debug("sessions_jsonl_read_failed", path=str(sessions_jsonl), exc_info=True)
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if composite_hash and entry.get("recipe_composite_hash") != composite_hash:
            continue
        if recipe_name and entry.get("recipe_name") != recipe_name:
            continue
        if not composite_hash and not recipe_name:
            continue
        matches.append(entry)
    return sorted(matches, key=lambda e: e.get("timestamp", ""), reverse=True)


def check_rerun_detection(
    sessions_jsonl: Path,
    *,
    composite_hash: str,
) -> dict[str, Any] | None:
    """Check if this exact recipe composite hash has been run before.

    Returns a suggestion dict if a prior run is found, None otherwise.
    """
    prior = find_prior_runs(sessions_jsonl, composite_hash=composite_hash)
    if not prior:
        return None
    last = prior[0]
    return {
        "rule": "duplicate-run-detected",
        "severity": "info",
        "message": (
            f"This exact recipe version was last run on "
            f"{last.get('timestamp', 'unknown')} "
            f"(session {last.get('session_id', 'unknown')})."
        ),
    }
