"""Recipe contract staleness detection and MCP suggestions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.core import SkillResolver

from autoskillit.recipe._contracts_card import _generate_recipe_card_for_recipe
from autoskillit.recipe._contracts_manifest import compute_skill_hash, load_bundled_manifest
from autoskillit.recipe._contracts_types import StaleItem
from autoskillit.recipe.staleness_cache import (
    StalenessEntry,
    compute_recipe_hash,
    read_staleness_cache,
    write_staleness_cache,
)


def check_contract_staleness(
    contract: dict[str, Any] | Any,
    *,
    recipe_path: Path | None = None,
    cache_path: Path | None = None,
    skills_dir: Path | None = None,
    resolver: SkillResolver | None = None,
    stored_card: Any = None,
) -> list[StaleItem]:
    """Check a pipeline contract for staleness against the current manifest.

    When ``stored_card`` is provided and ``contract`` is a ``Recipe``, compares
    block fingerprints from the stored card against the current recipe's blocks.
    Returns ``StaleItem`` entries with ``reason='block_composition_drift'`` for
    any block whose fingerprint has changed.  This path does not perform manifest
    or skill-hash checks — it is a pure structural comparison.

    When ``recipe_path`` and ``cache_path`` are both provided, a disk-backed
    cache keyed by recipe content hash + manifest version is consulted first.
    A cache hit with ``is_stale=False`` returns [] immediately without reading
    any SKILL.md files. Stale cache hits fall through to re-compute StaleItem
    details. The result is written back to the cache on every cache miss.

    When ``skills_dir`` is None, the bundled skills directory is used for hash
    comparison.

    When ``contract`` is a ``Recipe`` but ``stored_card`` is ``None``, no
    comparison baseline is available and [] is returned immediately.  This is
    expected during initial card generation before a stored card exists.

    Returns a list of StaleItem entries indicating what changed.
    """
    if stored_card is not None:
        recipe_obj = contract if hasattr(contract, "steps") else None
        if recipe_obj is not None:
            current_card = _generate_recipe_card_for_recipe(recipe_obj)
            current_fps = {fp.name: fp for fp in current_card.block_fingerprints}
            stale_items: list[StaleItem] = []
            for stored_fp in stored_card.block_fingerprints:
                current_fp = current_fps.get(stored_fp.name)
                if current_fp is None:
                    stale_items.append(
                        StaleItem(
                            skill=stored_fp.name,
                            reason="block_composition_drift",
                            stored_value=repr(stored_fp),
                            current_value="(block removed)",
                        )
                    )
                elif current_fp != stored_fp:
                    stale_items.append(
                        StaleItem(
                            skill=stored_fp.name,
                            reason="block_composition_drift",
                            stored_value=repr(stored_fp),
                            current_value=repr(current_fp),
                        )
                    )
            return stale_items

    if hasattr(contract, "steps"):
        return []

    stale: list[StaleItem] = []

    if recipe_path is not None:
        stored_hash = contract.get("recipe_source_hash")
        if stored_hash is not None:
            current_hash = compute_recipe_hash(recipe_path)
            if stored_hash != current_hash:
                stale.append(
                    StaleItem(
                        skill="<recipe>",
                        reason="recipe_content_drift",
                        stored_value=stored_hash,
                        current_value=current_hash,
                    )
                )

    manifest = load_bundled_manifest()
    current_version = manifest["version"]
    cached: StalenessEntry | None = None

    if recipe_path is not None and cache_path is not None:
        cached = read_staleness_cache(cache_path, recipe_path.stem)
        if cached is not None:
            current_hash = compute_recipe_hash(recipe_path)
            if cached.recipe_hash == current_hash and cached.manifest_version == current_version:
                if not cached.is_stale:
                    return stale

    stored_version = contract.get("bundled_manifest_version", "")
    if stored_version != current_version:
        stale.append(
            StaleItem(
                skill="(manifest)",
                reason="version_mismatch",
                stored_value=stored_version,
                current_value=current_version,
            )
        )

    if skills_dir is not None:
        _resolver = None
        effective_skills_dir: Path | None = skills_dir
    else:
        if resolver is None:
            from autoskillit.workspace import DefaultSkillResolver

            resolver = DefaultSkillResolver()
        _resolver = resolver
        effective_skills_dir = None
    for skill_name, stored_hash in contract.get("skill_hashes", {}).items():
        if effective_skills_dir is not None:
            current_hash = compute_skill_hash(skill_name, skills_dir=effective_skills_dir)
        else:
            if _resolver is None:
                raise RuntimeError(
                    "check_staleness called without effective_skills_dir or resolver"
                )
            info = _resolver.resolve(skill_name)
            current_hash = (
                compute_skill_hash(skill_name, skills_dir=info.path.parent.parent)
                if info is not None
                else ""
            )
        if current_hash and stored_hash != current_hash:
            stale.append(
                StaleItem(
                    skill=skill_name,
                    reason="hash_mismatch",
                    stored_value=stored_hash,
                    current_value=current_hash,
                )
            )

    if recipe_path is not None and cache_path is not None:
        file_hash = compute_recipe_hash(recipe_path)
        prior_triage: str | None = None
        if (
            cached is not None
            and cached.recipe_hash == file_hash
            and cached.manifest_version == current_version
        ):
            prior_triage = cached.triage_result
        write_staleness_cache(
            cache_path,
            recipe_path.stem,
            StalenessEntry(
                recipe_hash=file_hash,
                manifest_version=current_version,
                is_stale=bool(stale),
                triage_result=prior_triage,
                checked_at=datetime.now(UTC).isoformat(),
            ),
        )

    return stale


def stale_to_suggestions(stale: list[StaleItem]) -> list[dict[str, str]]:
    """Convert stale contract items to MCP suggestion dicts."""
    suggestions: list[dict[str, str]] = []
    for item in stale:
        suggestions.append(
            {
                "rule": "stale-contract",
                "severity": "warning",
                "step": item.skill,
                "skill": item.skill,
                "reason": item.reason,
                "stored_value": item.stored_value,
                "current_value": item.current_value,
                "message": (
                    f"Contract is stale: {item.reason} for "
                    f"'{item.skill}' (stored={item.stored_value}, "
                    f"current={item.current_value}). Consider "
                    f"regenerating the contract."
                ),
            }
        )
    return suggestions
